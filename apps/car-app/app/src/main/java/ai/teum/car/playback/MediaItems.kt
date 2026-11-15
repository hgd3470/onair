package ai.teum.car.playback

import ai.teum.car.data.PlaylistItem
import ai.teum.car.data.Topics
import android.net.Uri
import android.os.Bundle
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.MimeTypes

/** 편성 항목 ↔ MediaItem. 컨트롤러→세션 전달 중 uri 가 빠지므로 requestMetadata 에도 싣는다. */
object MediaItems {
    private const val KEY_KIND = "teum.kind"
    private const val KEY_CATEGORY = "teum.category"
    private const val KEY_DURATION = "teum.duration_sec"
    private const val KEY_AUDIO_URI = "teum.audio_uri"
    private const val KEY_SUBTITLE = "teum.subtitle"
    private const val KEY_SOURCES = "teum.sources"
    private const val KEY_ROUTINE_STEP = "teum.routine_step"
    private const val KEY_SELECTION_ID = "teum.selection_id"
    private const val KEY_TEXT = "teum.text"
    private const val KEY_LIVE_TTS = "teum.live_tts"

    fun toMediaItem(item: PlaylistItem, resolvedUrl: String): MediaItem {
        val extras = Bundle().apply {
            putString(KEY_KIND, item.kind)
            putString(KEY_CATEGORY, item.category)
            putDouble(KEY_DURATION, item.durationSec)
            putString(KEY_AUDIO_URI, item.audioUri)
            putString(KEY_SUBTITLE, item.subtitle)
            putStringArrayList(KEY_SOURCES, ArrayList(item.sources))
            item.routineStep?.let { putString(KEY_ROUTINE_STEP, it) }
            item.selectionId?.let { putString(KEY_SELECTION_ID, it) }
            if (item.isBriefing) putString(KEY_TEXT, item.text)
            putBoolean(KEY_LIVE_TTS, item.liveTts)
        }
        val uri = Uri.parse(resolvedUrl)
        val artist = when {
            item.isAd -> "광고"
            item.isBriefing -> "온에어 브리핑 · AI 음성"
            item.isLongform -> "깊이 듣기 · 두 사람 팟캐스트"
            else -> Topics.labelForCategory(item.category) ?: "온에어"
        }
        return MediaItem.Builder()
            .setMediaId(item.contentId)
            .setUri(uri)
            .setMimeType(mimeFor(item.kind))
            .setRequestMetadata(MediaItem.RequestMetadata.Builder().setMediaUri(uri).build())
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(item.title)
                    .setArtist(artist)
                    .setSubtitle(item.subtitle.ifEmpty { null })
                    .setAlbumTitle("온에어")
                    .setIsPlayable(true)
                    .setIsBrowsable(false)
                    .setExtras(extras)
                    .build(),
            )
            .build()
    }

    /** 세션 쪽에서 uri 를 복원한다. */
    fun withPlayableUri(item: MediaItem): MediaItem {
        if (item.localConfiguration != null) return item
        val uri = item.requestMetadata.mediaUri ?: return item
        return item.buildUpon().setUri(uri).setMimeType(mimeFor(kind(item))).build()
    }

    fun toPlaylistItem(item: MediaItem): PlaylistItem {
        val extras = item.mediaMetadata.extras
        return PlaylistItem(
            contentId = item.mediaId,
            kind = extras?.getString(KEY_KIND) ?: "story",
            title = item.mediaMetadata.title?.toString() ?: "",
            category = extras?.getString(KEY_CATEGORY) ?: "",
            durationSec = extras?.getDouble(KEY_DURATION) ?: 0.0,
            audioUri = extras?.getString(KEY_AUDIO_URI) ?: "",
            subtitle = extras?.getString(KEY_SUBTITLE) ?: "",
            sources = extras?.getStringArrayList(KEY_SOURCES).orEmpty(),
            routineStep = extras?.getString(KEY_ROUTINE_STEP),
            selectionId = extras?.getString(KEY_SELECTION_ID),
            text = extras?.getString(KEY_TEXT) ?: "",
            liveTts = extras?.getBoolean(KEY_LIVE_TTS) ?: false,
        )
    }

    private fun kind(item: MediaItem?): String? = item?.mediaMetadata?.extras?.getString(KEY_KIND)

    fun isAd(item: MediaItem?): Boolean = kind(item) == PlaylistItem.KIND_AD

    /** 브리핑은 콘텐츠가 아니라 청취 이벤트를 보내지 않는다. */
    fun isBriefing(item: MediaItem?): Boolean = kind(item) == PlaylistItem.KIND_BRIEFING

    /** 롱폼도 카탈로그 밖이라 청취 이벤트를 보내지 않는다(서버가 unknown_content 로 거절). */
    fun isLongform(item: MediaItem?): Boolean = kind(item) == PlaylistItem.KIND_LONGFORM

    /** 무음 자리 + Pleos TTS 로 읽는 브리핑인지. */
    fun isLiveTts(item: MediaItem?): Boolean = item?.mediaMetadata?.extras?.getBoolean(KEY_LIVE_TTS) == true

    fun briefingText(item: MediaItem?): String = item?.mediaMetadata?.extras?.getString(KEY_TEXT) ?: ""

    fun selectionId(item: MediaItem?): String? = item?.mediaMetadata?.extras?.getString(KEY_SELECTION_ID)

    fun fallbackDurationSec(item: MediaItem): Double =
        item.mediaMetadata.extras?.getDouble(KEY_DURATION) ?: 0.0

    private fun mimeFor(kind: String?) =
        if (kind == PlaylistItem.KIND_BRIEFING) MimeTypes.AUDIO_WAV else MimeTypes.AUDIO_MPEG
}
