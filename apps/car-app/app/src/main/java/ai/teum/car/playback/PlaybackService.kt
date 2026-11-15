package ai.teum.car.playback

import ai.teum.car.BuildConfig
import ai.teum.car.MainActivity
import ai.teum.car.TeumApp
import android.app.PendingIntent
import android.content.Intent
import android.util.Log
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.datasource.HttpDataSource
import androidx.media3.datasource.TransferListener
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * Media3 MediaSessionService.
 * - 오디오 포커스는 ExoPlayer 가 처리한다(handleAudioFocus=true). 통화 등 포커스 상실 시 멈추고 이벤트를 남긴다.
 * - 서비스 시작·연결만으로 재생하지 않는다. 재생은 화면에서 사용자가 시작한다.
 * - 재생 재개(onPlaybackResumption)를 지원하지 않아 시스템이 앱을 자동 재생시키지 않는다.
 * - 목적지 도착(NaviHelper onDestinationArrived): 듣던 편까지만 재생하고 다음 편으로 넘어가지 않는다.
 */
class PlaybackService : MediaSessionService() {
    private var session: MediaSession? = null
    private var tracker: PlaybackEventTracker? = null
    private var exo: ExoPlayer? = null
    private var liveBriefing: LiveBriefing? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    override fun onCreate() {
        super.onCreate()
        val app = TeumApp.from(this)
        val exo = ExoPlayer.Builder(this)
            .setMediaSourceFactory(DefaultMediaSourceFactory(audioDataSourceFactory()))
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(C.USAGE_MEDIA)
                    .setContentType(C.AUDIO_CONTENT_TYPE_SPEECH)
                    .build(),
                /* handleAudioFocus = */ true,
            )
            .setHandleAudioBecomingNoisy(true)
            .setWakeMode(C.WAKE_MODE_NETWORK)
            .build()
        exo.setPlaybackSpeed(app.store.speed)
        this.exo = exo

        val tracker = PlaybackEventTracker(exo, app.events, app).also { this.tracker = it }
        exo.addListener(tracker)
        tracker.start()
        // 브리핑(Pleos TTS)도 같은 큐에서 재생한다.
        app.speaker.start()
        exo.addListener(LiveBriefing(exo, app.speaker).also { liveBriefing = it })

        val openApp = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        session = MediaSession.Builder(this, TeumPlayer(exo, tracker))
            .setSessionActivity(openApp)
            .setCallback(SessionCallback)
            .build()

        app.vehicle.start()
        scope.launch { app.vehicle.navi.arrivals.collect { onArrived() } }
    }

    /**
     * 도착: 지금 편 뒤의 항목을 지워 끝나면 멈추게 한다(끝까지 들으면 complete).
     * 남은 길이가 길면(ARRIVAL_TAIL_MAX_MS 초과) 바로 멈추고 skip(arrival)을 남긴다. 광고는 끝까지 둔다.
     */
    private fun onArrived() {
        val player = exo ?: return
        if (player.mediaItemCount == 0) return
        val index = player.currentMediaItemIndex.coerceAtLeast(0)
        if (index + 1 < player.mediaItemCount) player.removeMediaItems(index + 1, player.mediaItemCount)
        val duration = player.duration
        val leftMs = if (duration == C.TIME_UNSET) 0L else
            ((duration - player.currentPosition) / player.playbackParameters.speed).toLong()
        val isAd = MediaItems.isAd(player.currentMediaItem)
        if (player.isPlaying && !isAd && leftMs > ARRIVAL_TAIL_MAX_MS) {
            tracker?.stopForArrival()
            player.pause()
        }
        Log.i(AUDIO_TAG, "도착: 듣던 편까지만 재생 (남은 ${leftMs / 1000}초)")
    }

    /** 음원 스트리밍. 요청 주소와 HTTP 응답 코드를 logcat(TeumAudio)에 남긴다. */
    private fun audioDataSourceFactory(): DataSource.Factory {
        val http = DefaultHttpDataSource.Factory()
            .setUserAgent("teum-car/${BuildConfig.VERSION_NAME}")
            .setTransferListener(object : TransferListener {
                override fun onTransferInitializing(source: DataSource, dataSpec: DataSpec, isNetwork: Boolean) = Unit
                override fun onTransferStart(source: DataSource, dataSpec: DataSpec, isNetwork: Boolean) {
                    val code = (source as? HttpDataSource)?.responseCode ?: -1
                    Log.i(AUDIO_TAG, "GET ${dataSpec.uri} -> $code (offset=${dataSpec.position})")
                }
                override fun onBytesTransferred(source: DataSource, dataSpec: DataSpec, isNetwork: Boolean, bytesTransferred: Int) = Unit
                override fun onTransferEnd(source: DataSource, dataSpec: DataSpec, isNetwork: Boolean) = Unit
            })
        return DefaultDataSource.Factory(this, http)
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? = session

    /** 앱을 최근 목록에서 지우면 정지(pause 이벤트 기록) 후 서비스를 끝낸다. */
    override fun onTaskRemoved(rootIntent: Intent?) {
        session?.player?.pause()
        stopSelf()
    }

    override fun onDestroy() {
        scope.cancel()
        tracker?.stop()
        liveBriefing?.release()
        session?.run {
            player.release()
            release()
        }
        session = null
        exo = null
        TeumApp.from(this).currentPlayback = null
        super.onDestroy()
    }

    private companion object {
        const val AUDIO_TAG = "TeumAudio"
        /** 도착 뒤 듣던 편을 마저 들을 수 있는 최대 남은 길이. 이야기는 대부분 1분 남짓이다. */
        const val ARRIVAL_TAIL_MAX_MS = 3 * 60 * 1000L
    }

    private object SessionCallback : MediaSession.Callback {
        override fun onAddMediaItems(
            mediaSession: MediaSession,
            controller: MediaSession.ControllerInfo,
            mediaItems: MutableList<MediaItem>,
        ): ListenableFuture<MutableList<MediaItem>> =
            Futures.immediateFuture(mediaItems.map(MediaItems::withPlayableUri).toMutableList())
    }
}
