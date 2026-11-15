package ai.teum.car.playback

import ai.teum.car.data.PlaylistItem
import android.content.Context
import android.net.Uri
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import java.util.Locale
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.ceil

/**
 * 브리핑 문장(text)을 이야기와 같은 ExoPlayer 큐에 넣을 수 있게 준비한다.
 * MediaSession·스티어링 키(다음/이전/일시정지)·오디오 포커스가 이야기와 똑같이 동작한다.
 *
 * 1) Pleos TextToSpeech(차량 음성 서비스)가 되면: 큐에는 무음 자리를 넣고, 그 항목이 재생되는 동안
 *    LiveBriefing 이 Pleos TTS 로 읽는다. (Pleos TTS 는 파일로 만들 수 없고 직접 소리만 낸다.)
 * 2) 아니면 Android TextToSpeech(Locale.KOREAN)로 음성 파일을 만들어 그대로 재생한다.
 *
 * 파일은 캐시에만 두고 새 큐를 만들 때 지난 파일을 지운다.
 */
class BriefingVoice(context: Context, private val speaker: PleosSpeaker) {
    private val appContext = context.applicationContext
    private val dir = File(appContext.cacheDir, "briefing")
    private val mutex = Mutex()
    private var tts: TextToSpeech? = null
    private val waiting = ConcurrentHashMap<String, CompletableDeferred<Boolean>>()

    /** 재생할 수 있는 브리핑 항목을 돌려준다. 둘 다 안 되면 IOException. */
    suspend fun prepare(item: PlaylistItem, speed: Float): PlaylistItem {
        if (item.text.isBlank()) throw IOException("empty briefing text")
        if (speaker.awaitReady()) {
            // 다 읽으면 LiveBriefing 이 다음으로 넘기므로, 자리는 예상 길이보다 넉넉하게 둔다.
            val seconds = ceil((item.durationSec * SILENCE_MARGIN + 3) * speed.coerceAtLeast(1f)).toInt()
            return item.copy(audioUri = silence(seconds), liveTts = true)
        }
        return item.copy(audioUri = synthesize(item.text), liveTts = false)
    }

    /** 지금 큐에 없는 지난 브리핑 파일을 지운다. 무음 자리 파일은 다시 쓰므로 남긴다. */
    suspend fun clearExcept(keep: Collection<String>) = withContext(Dispatchers.IO) {
        val keepNames = keep.mapNotNull { Uri.parse(it).lastPathSegment }.toSet()
        dir.listFiles()?.filter { it.name !in keepNames && !it.name.startsWith(SILENCE_PREFIX) }?.forEach { it.delete() }
    }

    /** 8kHz·16bit·모노 무음 WAV. 길이별로 한 번만 만든다. */
    private suspend fun silence(seconds: Int): String = withContext(Dispatchers.IO) {
        dir.mkdirs()
        val file = File(dir, "$SILENCE_PREFIX$seconds.wav")
        if (!file.isFile) {
            val tmp = File(dir, "tmp-${UUID.randomUUID()}.wav")
            val rate = 8_000
            val dataBytes = rate * 2 * seconds
            RandomAccessFile(tmp, "rw").use { f ->
                f.write(wavHeader(rate, dataBytes))
                f.setLength(44L + dataBytes) // 나머지는 0(무음)
            }
            tmp.renameTo(file)
        }
        Uri.fromFile(file).toString()
    }

    private fun wavHeader(rate: Int, dataBytes: Int): ByteArray {
        fun le32(v: Int) = byteArrayOf(v.toByte(), (v shr 8).toByte(), (v shr 16).toByte(), (v shr 24).toByte())
        fun le16(v: Int) = byteArrayOf(v.toByte(), (v shr 8).toByte())
        return "RIFF".toByteArray() + le32(36 + dataBytes) + "WAVE".toByteArray() +
            "fmt ".toByteArray() + le32(16) + le16(1) + le16(1) + le32(rate) + le32(rate * 2) + le16(2) + le16(16) +
            "data".toByteArray() + le32(dataBytes)
    }

    /** Android TTS 로 text 를 음성 파일로 만들어 file:// uri 를 돌려준다. */
    private suspend fun synthesize(text: String): String = mutex.withLock {
        val engine = engine()
        withContext(Dispatchers.IO) { dir.mkdirs() }
        val file = File(dir, "${UUID.randomUUID()}.wav")
        val id = file.name
        val done = CompletableDeferred<Boolean>()
        waiting[id] = done
        val ok = try {
            val r = engine.synthesizeToFile(text, Bundle(), file, id)
            if (r != TextToSpeech.SUCCESS) false else withTimeout(SYNTH_TIMEOUT_MS) { done.await() }
        } catch (e: Exception) {
            Log.w(TAG, "음성 만들기 실패: ${e.message}")
            false
        } finally {
            waiting.remove(id)
        }
        if (!ok || !file.isFile || file.length() < 100) {
            file.delete()
            throw IOException("tts synthesis failed")
        }
        Uri.fromFile(file).toString()
    }

    private suspend fun engine(): TextToSpeech {
        tts?.let { return it }
        val ready = CompletableDeferred<Int>()
        val engine = withContext(Dispatchers.Main) { TextToSpeech(appContext) { status -> ready.complete(status) } }
        val status = try {
            withTimeout(INIT_TIMEOUT_MS) { ready.await() }
        } catch (e: Exception) {
            TextToSpeech.ERROR
        }
        if (status != TextToSpeech.SUCCESS) {
            runCatching { engine.shutdown() }
            throw IOException("tts engine unavailable")
        }
        val lang = engine.setLanguage(Locale.KOREAN)
        if (lang == TextToSpeech.LANG_MISSING_DATA || lang == TextToSpeech.LANG_NOT_SUPPORTED) {
            engine.shutdown()
            throw IOException("korean tts not supported")
        }
        engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) = Unit
            override fun onDone(utteranceId: String?) { waiting[utteranceId]?.complete(true) }
            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) { waiting[utteranceId]?.complete(false) }
            override fun onError(utteranceId: String?, errorCode: Int) { waiting[utteranceId]?.complete(false) }
        })
        tts = engine
        return engine
    }

    private companion object {
        const val TAG = "TeumVoice"
        const val SILENCE_PREFIX = "silence-"
        const val SILENCE_MARGIN = 1.3
        const val INIT_TIMEOUT_MS = 5_000L
        const val SYNTH_TIMEOUT_MS = 20_000L
    }
}
