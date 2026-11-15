package ai.teum.car.data

import android.util.Log
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.io.IOException
import java.time.OffsetDateTime
import java.time.format.DateTimeFormatter
import java.util.UUID

/** 이벤트 type 값 (listening 규격 v0.21). 브리핑은 콘텐츠가 아니라 보내지 않는다(unknown_content). */
object EventType {
    const val PLAY = "play"
    const val POSITION = "position"
    const val PAUSE = "pause"
    const val RESUME = "resume"
    const val SKIP = "skip"
    const val COMPLETE = "complete"
    const val LIKE = "like"
    const val UNLIKE = "unlike"
    const val SAVE = "save"
    const val UNSAVE = "unsave"
    const val AD_START = "ad_start"
    const val AD_COMPLETE = "ad_complete"
    const val AD_ERROR = "ad_error"

    val reactions = setOf(LIKE, UNLIKE, SAVE, UNSAVE)
    val ads = setOf(AD_START, AD_COMPLETE, AD_ERROR)
}

/** skip 이벤트의 skip_reason (S2-10). 서버는 user_next 만 넘김 감점에 쓰고 나머지는 중립이다. */
object SkipReason {
    const val USER_NEXT = "user_next" // 다음 버튼·스티어링 휠 다음 키
    const val USER_PREV = "user_prev" // 이전 버튼·키
    const val ARRIVAL = "arrival"     // 목적지 도착으로 멈춤
    const val ERROR = "error"         // 재생 오류
}

/** 이벤트 본문을 만든다. seq 와 시각은 호출 시점에 확정한다(재전송 때 바꾸지 않음). */
object EventFactory {
    private val tsFormat = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSSxxx")

    fun nowIso(now: OffsetDateTime = OffsetDateTime.now()): String = now.format(tsFormat)

    fun build(
        type: String,
        contentId: String,
        seq: Long,
        playbackId: String?,
        positionSec: Double?,
        playbackRate: Float,
        clientTs: String = nowIso(),
        eventId: String = UUID.randomUUID().toString(),
        skipReason: String? = null,
        selectionId: String? = null,
    ): JSONObject {
        val o = JSONObject()
            .put("event_id", eventId)
            .put("content_id", contentId)
            .put("seq", seq)
            .put("type", type)
            .put("client_ts", clientTs)
        if (playbackId != null) o.put("playback_id", playbackId)
        when (type) {
            in EventType.reactions -> Unit // 위치·배속 없음
            in EventType.ads -> o.put("position_sec", round1(positionSec ?: 0.0)) // ad_* 는 version·rate 없음
            else -> {
                o.put("position_sec", round1(positionSec ?: 0.0))
                o.put("playback_rate", playbackRate.toDouble())
                o.put("content_version", 1)
            }
        }
        if (type == EventType.SKIP && skipReason != null) o.put("skip_reason", skipReason)
        if (type == EventType.PLAY && selectionId != null) o.put("selection_id", selectionId)
        return o
    }

    private fun round1(v: Double) = Math.round(v.coerceAtLeast(0.0) * 10) / 10.0
}

/**
 * POST /events 단건 전송. 하나의 소비 코루틴이 만든 순서대로 보낸다.
 * 오프라인 큐는 이번 범위가 아니므로 메모리에서 짧게 재시도한 뒤 포기한다.
 */
class EventReporter(
    private val api: ApiClient,
    private val store: DeviceStore,
    private val auth: Auth,
    scope: CoroutineScope,
) {
    private class Pending(val body: JSONObject, val done: CompletableDeferred<Boolean>?)

    private val channel = Channel<Pending>(Channel.UNLIMITED)

    init {
        scope.launch(Dispatchers.IO) {
            for (p in channel) {
                val ok = runCatching { deliver(p.body) }.getOrElse {
                    Log.w(TAG, "전송 실패 ${p.body.optString("type")}: ${it.message}")
                    false
                }
                p.done?.complete(ok)
            }
        }
    }

    /** 재생 이벤트. 메인 스레드에서 호출해도 된다(즉시 반환). */
    fun playback(
        type: String,
        contentId: String,
        playbackId: String,
        positionSec: Double,
        rate: Float,
        skipReason: String? = null,
        selectionId: String? = null,
    ) {
        val body = EventFactory.build(
            type, contentId, store.nextSeq(), playbackId, positionSec, rate,
            skipReason = skipReason, selectionId = selectionId,
        )
        enqueue(body, null)
    }

    fun ad(type: String, adId: String, playbackId: String, positionSec: Double) {
        enqueue(EventFactory.build(type, adId, store.nextSeq(), playbackId, positionSec, 1.0f), null)
    }

    /** 좋아요·보관. 서버가 받았는지 결과를 돌려준다. */
    suspend fun reaction(type: String, contentId: String, playbackId: String?): Boolean {
        val done = CompletableDeferred<Boolean>()
        enqueue(EventFactory.build(type, contentId, store.nextSeq(), playbackId, null, 1.0f), done)
        return done.await()
    }

    private fun enqueue(body: JSONObject, done: CompletableDeferred<Boolean>?) {
        val extra = listOfNotNull(
            body.optString("skip_reason").ifEmpty { null }?.let { "skip_reason=$it" },
            body.optString("selection_id").ifEmpty { null }?.let { "selection_id=$it" },
        ).joinToString(" ")
        Log.d(TAG, "event ${body.optString("type")} seq=${body.optLong("seq")} pos=${body.opt("position_sec")} $extra")
        channel.trySend(Pending(body, done))
    }

    private suspend fun deliver(body: JSONObject): Boolean {
        var creds = auth.ensure()
        var renewed = false
        var attempt = 0
        while (true) {
            try {
                api.postEvent(creds.token, body)
                return true
            } catch (e: HttpException) {
                when {
                    e.code == 401 && !renewed -> {
                        creds = auth.renew(creds.token)
                        renewed = true
                        continue
                    }
                    e.code == 404 || e.code == 422 || e.code == 401 -> {
                        Log.w(TAG, "이벤트 버림 ${e.code}: ${e.body.take(200)}")
                        return false
                    }
                    else -> if (attempt >= MAX_RETRY) return false
                }
            } catch (e: IOException) {
                if (attempt >= MAX_RETRY) return false
            }
            attempt++
            delay(RETRY_DELAY_MS * attempt)
        }
    }

    private companion object {
        const val TAG = "TeumEvents"
        const val MAX_RETRY = 3
        const val RETRY_DELAY_MS = 1_000L
    }
}
