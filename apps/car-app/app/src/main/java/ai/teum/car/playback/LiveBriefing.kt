package ai.teum.car.playback

import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer

/**
 * 큐의 브리핑 자리(무음 항목)가 재생되는 동안 Pleos TTS 로 문장을 읽는다.
 * - 재생 중이면 읽고, 일시정지·포커스 상실이면 멈춘다. 다시 재생하면 그 브리핑을 처음부터 읽는다.
 * - 다 읽으면 다음 항목으로 넘긴다(마지막이면 끝낸다). 이전/다음·스티어링 키는 큐가 그대로 처리한다.
 * - 브리핑은 콘텐츠가 아니라 청취 이벤트가 없다.
 */
class LiveBriefing(private val exo: ExoPlayer, private val speaker: PleosSpeaker) : Player.Listener {
    /** 지금 읽고 있는 큐 위치. */
    private var speakingIndex: Int? = null
    /** 다 읽은 큐 위치(같은 항목을 다시 읽지 않게). 다른 항목으로 가면 지운다. */
    private var finishedIndex: Int? = null

    init {
        speaker.onFinished = ::onFinished
    }

    override fun onMediaItemTransition(mediaItem: androidx.media3.common.MediaItem?, reason: Int) {
        stopSpeaking()
        finishedIndex = null
        sync()
    }

    override fun onPositionDiscontinuity(
        oldPosition: Player.PositionInfo,
        newPosition: Player.PositionInfo,
        reason: Int,
    ) {
        // 끝난 브리핑을 처음으로 돌려 다시 들으면(재생 버튼) 다시 읽는다.
        if (reason == Player.DISCONTINUITY_REASON_SEEK && newPosition.positionMs < RESTART_THRESHOLD_MS) {
            finishedIndex = null
        }
    }

    override fun onEvents(player: Player, events: Player.Events) = sync()

    private fun sync() {
        val item = exo.currentMediaItem
        if (!MediaItems.isLiveTts(item)) {
            stopSpeaking()
            return
        }
        val index = exo.currentMediaItemIndex
        val shouldSpeak = exo.playWhenReady &&
            exo.playbackSuppressionReason == Player.PLAYBACK_SUPPRESSION_REASON_NONE &&
            exo.playbackState == Player.STATE_READY
        when {
            shouldSpeak && speakingIndex == null && finishedIndex != index -> {
                if (exo.currentPosition > RESTART_THRESHOLD_MS) {
                    exo.seekTo(0) // 멈췄던 브리핑은 처음부터 다시 읽는다(위치 변경 뒤 다시 sync)
                    return
                }
                speakingIndex = index
                speaker.speak(MediaItems.briefingText(item))
            }
            !shouldSpeak && speakingIndex != null && exo.playbackState != Player.STATE_BUFFERING -> stopSpeaking()
        }
    }

    private fun onFinished(ok: Boolean) {
        val index = speakingIndex ?: return
        speakingIndex = null
        if (index != exo.currentMediaItemIndex) return
        finishedIndex = index
        if (exo.hasNextMediaItem()) {
            exo.seekToNextMediaItem()
        } else {
            exo.seekTo(index, exo.duration.coerceAtLeast(0)) // 마지막이면 끝으로
        }
    }

    private fun stopSpeaking() {
        if (speakingIndex == null) return
        speakingIndex = null
        speaker.stop()
    }

    fun release() {
        stopSpeaking()
        speaker.onFinished = null
    }

    private companion object {
        const val RESTART_THRESHOLD_MS = 500L
    }
}
