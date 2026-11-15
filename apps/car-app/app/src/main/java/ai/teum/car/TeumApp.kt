package ai.teum.car

import ai.teum.car.data.ApiClient
import ai.teum.car.data.Auth
import ai.teum.car.data.DeviceStore
import ai.teum.car.data.EventReporter
import ai.teum.car.playback.BriefingVoice
import ai.teum.car.playback.PleosSpeaker
import ai.teum.car.vehicle.VehicleContext
import android.app.Application
import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob

/** 앱 전역 의존성. 화면(Activity)과 재생 서비스가 같은 프로세스에서 공유한다. */
class TeumApp : Application() {
    val appScope = CoroutineScope(SupervisorJob())
    val store by lazy { DeviceStore(this) }
    val api by lazy { ApiClient(BuildConfig.SERVER_ORIGIN, BuildConfig.API_PREFIX) }
    val auth by lazy { Auth(api, store, BuildConfig.VERSION_NAME) }
    val events by lazy { EventReporter(api, store, auth, appScope) }
    val vehicle by lazy { VehicleContext(this, appScope) }
    val speaker by lazy { PleosSpeaker(this) }
    val voice by lazy { BriefingVoice(this, speaker) }

    /** 현재 재생 세션(playback_id). 좋아요·보관 이벤트에 붙인다. */
    @Volatile
    var currentPlayback: Pair<String, String>? = null // content_id to playback_id

    fun playbackIdFor(contentId: String): String? =
        currentPlayback?.takeIf { it.first == contentId }?.second

    companion object {
        fun from(context: Context) = context.applicationContext as TeumApp
    }
}
