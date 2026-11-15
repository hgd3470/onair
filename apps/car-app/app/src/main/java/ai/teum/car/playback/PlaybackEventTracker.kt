package ai.teum.car.playback

import ai.teum.car.TeumApp
import ai.teum.car.data.EventReporter
import ai.teum.car.data.EventType
import ai.teum.car.data.SkipReason
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import java.util.UUID

/**
 * 플레이어 상태를 청취 이벤트로 바꾼다.
 * - play: 콘텐츠의 첫 재생(새 playback_id) / resume: 같은 재생의 재개
 * - pause: 사용자 정지·포커스 상실(통화 등)
 * - position: 10초마다 + 정지·스킵·전환·배속 변경 직전
 * - skip: 다음(user_next)·이전(user_prev) 버튼·스티어링 키, 재생 오류(error), 도착으로 멈춤(arrival)
 * - complete: 끝까지 재생 (skip 이 아니다)
 * - play 에는 골라 듣기 목록이면 selection_id 를 싣는다.
 * - 광고(kind=ad)는 ad_start / ad_complete / ad_error 만 보낸다.
 * - 브리핑(kind=briefing)은 콘텐츠가 아니라 아무 이벤트도 보내지 않는다(서버가 unknown_content 로 거절).
 */
class PlaybackEventTracker(
    private val player: Player,
    private val events: EventReporter,
    private val app: TeumApp,
) : Player.Listener {

    private class Current(
        val contentId: String,
        val isAd: Boolean,
        val isBriefing: Boolean,
        val selectionId: String?,
        fallbackDurationSec: Double,
    ) {
        var playbackId: String? = null
        var userPaused = false
        var suppressed = false
        var pauseSent = false
        var ended = false
        var lastPositionSec = 0.0
        var durationSec = fallbackDurationSec
        val started get() = playbackId != null
    }

    private var current: Current? = null
    private var lastPositionReportAt = 0L
    private val handler = Handler(Looper.getMainLooper())
    private val tick = object : Runnable {
        override fun run() {
            sample()
            reportPeriodicPosition()
            handler.postDelayed(this, TICK_MS)
        }
    }

    fun start() {
        current = player.currentMediaItem?.let(::newCurrent)
        handler.post(tick)
    }

    fun stop() {
        handler.removeCallbacks(tick)
    }

    // ---- ForwardingPlayer 가 실제 동작 전에 부른다 ----

    fun beforeSkipNext() {
        val reason = if (player.playerError != null) SkipReason.ERROR else SkipReason.USER_NEXT
        skipCurrent(reason)
    }

    fun beforeSkipPrevious() = skipCurrent(SkipReason.USER_PREV)

    /** 도착해서 듣던 편을 중간에 멈춘다. 끝까지 들을 수 있으면 부르지 않는다(그때는 complete). */
    fun stopForArrival() = skipCurrent(SkipReason.ARRIVAL)

    private fun skipCurrent(reason: String) {
        val c = activeStory() ?: return
        sample()
        reportPosition(c)
        send(c, EventType.SKIP, c.lastPositionSec, skipReason = reason)
        c.ended = true
    }

    fun beforeSpeedChange() {
        val c = activeStory()?.takeIf { !it.pauseSent } ?: return
        sample()
        reportPosition(c)
    }

    fun afterSpeedChange() {
        val c = activeStory()?.takeIf { !it.pauseSent } ?: return
        reportPosition(c)
    }

    // ---- Player.Listener ----

    override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
        current?.let { prev ->
            if (prev.started && !prev.ended) {
                if (reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO ||
                    reason == Player.MEDIA_ITEM_TRANSITION_REASON_REPEAT
                ) {
                    finish(prev)
                } else if (!prev.isAd && !prev.isBriefing) {
                    reportPosition(prev) // 목록 교체 등 전환 직전
                    prev.ended = true
                }
            }
        }
        current = mediaItem?.let(::newCurrent)
        app.currentPlayback = null
        if (player.playWhenReady && player.playbackSuppressionReason == Player.PLAYBACK_SUPPRESSION_REASON_NONE) {
            begin()
        }
    }

    override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
        val c = current ?: return
        c.userPaused = !playWhenReady
        updatePauseState(c)
    }

    override fun onPlaybackSuppressionReasonChanged(playbackSuppressionReason: Int) {
        val c = current ?: return
        c.suppressed = playbackSuppressionReason != Player.PLAYBACK_SUPPRESSION_REASON_NONE
        updatePauseState(c)
    }

    /** 버퍼링이 끝나 실제로 소리가 나기 시작한 때 첫 play 를 보낸다. */
    override fun onIsPlayingChanged(isPlaying: Boolean) {
        if (isPlaying) begin()
    }

    override fun onPlaybackStateChanged(playbackState: Int) {
        sample()
        if (playbackState == Player.STATE_ENDED) {
            current?.takeIf { it.started && !it.ended }?.let(::finish)
        }
    }

    override fun onPositionDiscontinuity(
        oldPosition: Player.PositionInfo,
        newPosition: Player.PositionInfo,
        reason: Int,
    ) {
        // 끝난 콘텐츠를 처음부터 다시 듣는 경우 새 재생으로 시작한다.
        val c = current ?: return
        if (reason == Player.DISCONTINUITY_REASON_SEEK && c.ended &&
            oldPosition.mediaItemIndex == newPosition.mediaItemIndex
        ) {
            current = player.currentMediaItem?.let(::newCurrent)
            if (player.playWhenReady) begin()
        }
    }

    override fun onPlayerError(error: PlaybackException) {
        val c = current ?: return
        if (c.isAd && !c.ended) {
            val pid = c.playbackId ?: UUID.randomUUID().toString().also { c.playbackId = it }
            events.ad(EventType.AD_ERROR, c.contentId, pid, c.lastPositionSec)
            c.ended = true
        } else {
            skipCurrent(SkipReason.ERROR) // 듣던 중 오류로 끊김. 시작 전 오류는 play 가 없어 보내지 않는다.
        }
    }

    // ---- 내부 ----

    private fun newCurrent(item: MediaItem) = Current(
        contentId = item.mediaId,
        isAd = MediaItems.isAd(item),
        isBriefing = MediaItems.isBriefing(item) || MediaItems.isLongform(item), // 둘 다 이벤트 없음
        selectionId = MediaItems.selectionId(item),
        fallbackDurationSec = MediaItems.fallbackDurationSec(item) * speed(),
    )

    private fun activeStory(): Current? = current?.takeIf { it.started && !it.ended && !it.isAd && !it.isBriefing }

    private fun begin() {
        val c = current ?: return
        if (c.started || c.ended || !player.isPlaying) return
        val pid = UUID.randomUUID().toString()
        c.playbackId = pid
        c.userPaused = false
        c.suppressed = false
        c.pauseSent = false
        if (c.isBriefing) return // 브리핑은 이벤트 없음
        app.currentPlayback = c.contentId to pid
        if (c.isAd) {
            events.ad(EventType.AD_START, c.contentId, pid, 0.0)
        } else {
            events.playback(EventType.PLAY, c.contentId, pid, positionSec(), speed(), selectionId = c.selectionId)
        }
        lastPositionReportAt = SystemClock.elapsedRealtime()
    }

    private fun updatePauseState(c: Current) {
        val shouldPause = c.userPaused || c.suppressed
        if (!c.started) {
            if (!shouldPause) begin()
            return
        }
        if (c.ended || c.isBriefing) return
        if (shouldPause && !c.pauseSent) {
            c.pauseSent = true
            sample()
            if (!c.isAd) {
                reportPosition(c)
                send(c, EventType.PAUSE, c.lastPositionSec)
            }
        } else if (!shouldPause && c.pauseSent) {
            c.pauseSent = false
            if (!c.isAd) {
                send(c, EventType.RESUME, positionSec())
                lastPositionReportAt = SystemClock.elapsedRealtime()
            }
        }
    }

    private fun finish(c: Current) {
        c.ended = true
        if (c.isBriefing) return
        if (c.isAd) {
            events.ad(EventType.AD_COMPLETE, c.contentId, c.playbackId!!, c.durationSec)
        } else {
            send(c, EventType.COMPLETE, c.durationSec)
        }
    }

    private fun reportPeriodicPosition() {
        val c = activeStory() ?: return
        if (!player.isPlaying || c.pauseSent) return
        if (SystemClock.elapsedRealtime() - lastPositionReportAt >= POSITION_INTERVAL_MS) {
            reportPosition(c)
        }
    }

    private fun reportPosition(c: Current) {
        send(c, EventType.POSITION, c.lastPositionSec)
        lastPositionReportAt = SystemClock.elapsedRealtime()
    }

    private fun send(c: Current, type: String, positionSec: Double, skipReason: String? = null) {
        val pid = c.playbackId ?: return
        if (c.isBriefing) return
        events.playback(type, c.contentId, pid, positionSec, speed(), skipReason = skipReason)
    }

    /** 1초마다 현재 항목의 위치·길이를 기록해 둔다(전환 뒤에도 직전 값을 쓰기 위해). */
    private fun sample() {
        val c = current ?: return
        if (player.currentMediaItem?.mediaId != c.contentId) return
        c.lastPositionSec = positionSec()
        val d = player.duration
        if (d != C.TIME_UNSET && d > 0) c.durationSec = d / 1000.0
    }

    private fun positionSec() = player.currentPosition.coerceAtLeast(0) / 1000.0

    private fun speed() = player.playbackParameters.speed

    private companion object {
        const val TICK_MS = 1_000L
        const val POSITION_INTERVAL_MS = 10_000L
    }
}
