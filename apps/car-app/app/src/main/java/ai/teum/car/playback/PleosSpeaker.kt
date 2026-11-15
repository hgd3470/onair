package ai.teum.car.playback

import ai.pleos.playground.tts.TextToSpeech
import ai.pleos.playground.tts.constant.ErrorType
import ai.pleos.playground.tts.constant.Mode
import ai.pleos.playground.tts.listener.EventListener
import ai.pleos.playground.tts.listener.OnInitializeListener
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.withTimeoutOrNull
import java.util.Locale

/**
 * Pleos TextToSpeech(차량 음성 서비스, ON_DEVICE). 직접 소리를 내는 speak() 만 있어
 * 큐에는 무음 자리 항목을 넣고 그 항목이 재생되는 동안 읽는다(LiveBriefing).
 * Pleos 음성 서비스가 없는 기기(폰 등)에서는 준비 실패로 끝나고 Android TTS 를 쓴다.
 */
class PleosSpeaker(private val context: Context) {
    private var tts: TextToSpeech? = null
    private val ready = CompletableDeferred<Boolean>()
    private val main = Handler(Looper.getMainLooper())

    /** 읽기가 끝나면(true) 또는 오류(false). 메인 스레드에서 부른다. */
    var onFinished: ((Boolean) -> Unit)? = null

    private val events = object : EventListener {
        override fun onStart() = Unit
        override fun onReady() = Unit
        override fun onDone() { main.post { onFinished?.invoke(true) } }
        override fun onError(errorType: ErrorType) {
            Log.w(TAG, "읽기 오류: $errorType")
            main.post { onFinished?.invoke(false) }
        }
        override fun onStop() = Unit
        override fun onUpdatedRms(rms: Double) = Unit
    }

    fun start() = main.post {
        if (tts != null || ready.isCompleted) return@post
        try {
            val t = TextToSpeech(context.applicationContext, Mode.ON_DEVICE)
            // initialize 가 끝나야 리스너를 붙일 수 있다(SDK 내부 객체가 그때 생긴다).
            t.initialize(object : OnInitializeListener {
                override fun onSuccess() {
                    runCatching {
                        t.addEventListener(events)
                        t.setLocale(Locale.KOREA) // "ko" 만 주면 엔진이 거절한다(unsupported locale: ko-)
                    }.onFailure { Log.w(TAG, "Pleos TTS 설정 실패: ${it.message}") }
                    Log.i(TAG, "Pleos TTS 준비")
                    ready.complete(true)
                }

                override fun onFailure(errorType: ErrorType) {
                    Log.i(TAG, "Pleos TTS 사용 불가: $errorType")
                    ready.complete(false)
                }
            })
            tts = t
        } catch (t: Throwable) {
            Log.i(TAG, "Pleos TTS 사용 불가: ${t.message}")
            ready.complete(false)
        }
    }

    suspend fun awaitReady(timeoutMs: Long = 4_000): Boolean {
        start()
        return withTimeoutOrNull(timeoutMs) { ready.await() } ?: false
    }

    fun speak(text: String) {
        try {
            tts?.speak(text, true)
        } catch (t: Throwable) {
            Log.w(TAG, "speak 실패: ${t.message}")
            main.post { onFinished?.invoke(false) }
        }
    }

    fun stop() {
        runCatching { tts?.stop() }
    }

    private companion object {
        const val TAG = "TeumPleosTts"
    }
}
