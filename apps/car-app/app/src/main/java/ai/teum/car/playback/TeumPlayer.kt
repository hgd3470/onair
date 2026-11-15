package ai.teum.car.playback

import androidx.media3.common.ForwardingPlayer
import androidx.media3.common.PlaybackParameters
import androidx.media3.common.Player

/**
 * 세션에 노출하는 플레이어.
 * - 이전/다음·배속 변경 직전에 청취 이벤트(position·skip)를 남긴다.
 * - 광고 재생 중에는 이전/다음을 막는다(차량 버튼·시스템 미디어 UI 포함).
 * - "이전"은 항상 이전 항목으로 이동한다(첫 항목에서는 동작 없음).
 */
class TeumPlayer(player: Player, private val tracker: PlaybackEventTracker) : ForwardingPlayer(player) {

    private val adLocked: Boolean
        get() = MediaItems.isAd(currentMediaItem) && playerError == null

    override fun seekToNext() = next()
    override fun seekToNextMediaItem() = next()
    override fun seekToPrevious() = previous()
    override fun seekToPreviousMediaItem() = previous()

    private fun next() {
        if (adLocked || !hasNextMediaItem()) return
        tracker.beforeSkipNext()
        super.seekToNextMediaItem()
    }

    private fun previous() {
        if (adLocked || !hasPreviousMediaItem()) return
        tracker.beforeSkipPrevious()
        super.seekToPreviousMediaItem()
    }

    override fun setPlaybackSpeed(speed: Float) {
        if (speed == playbackParameters.speed) return
        tracker.beforeSpeedChange()
        super.setPlaybackSpeed(speed)
        tracker.afterSpeedChange()
    }

    override fun setPlaybackParameters(playbackParameters: PlaybackParameters) {
        if (playbackParameters == this.playbackParameters) return
        tracker.beforeSpeedChange()
        super.setPlaybackParameters(playbackParameters)
        tracker.afterSpeedChange()
    }

    override fun getAvailableCommands(): Player.Commands {
        val commands = super.getAvailableCommands()
        if (!adLocked) return commands
        return commands.buildUpon().removeAll(*SKIP_COMMANDS).build()
    }

    override fun isCommandAvailable(command: Int): Boolean {
        if (adLocked && command in SKIP_COMMANDS) return false
        return super.isCommandAvailable(command)
    }

    private companion object {
        val SKIP_COMMANDS = intArrayOf(
            Player.COMMAND_SEEK_TO_NEXT,
            Player.COMMAND_SEEK_TO_NEXT_MEDIA_ITEM,
            Player.COMMAND_SEEK_TO_PREVIOUS,
            Player.COMMAND_SEEK_TO_PREVIOUS_MEDIA_ITEM,
        )
    }
}
