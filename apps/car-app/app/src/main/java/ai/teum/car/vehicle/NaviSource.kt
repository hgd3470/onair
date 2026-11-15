package ai.teum.car.vehicle

import ai.pleos.playground.navi.constants.NavigationStatus
import ai.pleos.playground.navi.data.DestinationArrivedInfo
import ai.pleos.playground.navi.data.DestinationInfo
import ai.pleos.playground.navi.data.RouteStartInfo
import ai.pleos.playground.navi.helper.NaviHelper
import ai.pleos.playground.navi.helper.listener.NaviHelperEventListener
import android.content.Context
import android.util.Log
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withTimeoutOrNull

/** 경로 안내 중인 목적지까지 남은 시간(초)·거리(m). NaviHelper onDestinationInfo 값. */
data class Destination(val remainSec: Int, val remainM: Int?) {
    val remainMinutes: Int get() = ((remainSec + 59) / 60).coerceAtLeast(1)
}

/**
 * Pleos NaviHelper. 경로 안내 중일 때만 Destination 을 준다.
 * SDK·내비 서비스가 없거나(폰 에뮬레이터 등) 오류가 나면 조용히 null 로 남아 기존 동작(수동 시간)을 쓴다.
 */
class NaviSource(private val context: Context) {
    private var navi: NaviHelper? = null
    @Volatile private var ready = false
    /** 마지막으로 알게 된 경로 안내 여부. null = 아직 모름. */
    @Volatile private var routeActive: Boolean? = null
    @Volatile private var pending: CompletableDeferred<Destination?>? = null

    private val _destination = MutableStateFlow<Destination?>(null)
    val destination: StateFlow<Destination?> = _destination.asStateFlow()

    private val _arrivals = MutableSharedFlow<Unit>(extraBufferCapacity = 4)
    /** onDestinationArrived. 듣던 편까지만 재생하고 멈추는 데 쓴다. */
    val arrivals: SharedFlow<Unit> = _arrivals.asSharedFlow()

    fun start() {
        if (navi != null) return
        try {
            val helper = NaviHelper(context.applicationContext)
            helper.addListener(listener)
            navi = helper
            helper.initialize(
                {
                    ready = true
                    Log.i(TAG, "NaviHelper 연결")
                    request(helper)
                },
                { e -> Log.i(TAG, "NaviHelper 사용 불가: ${e.message}") },
            )
        } catch (t: Throwable) { // SDK 서비스 없음, 권한 없음 등
            Log.i(TAG, "NaviHelper 사용 불가: ${t.message}")
            navi = null
        }
    }

    /** 지금 값을 새로 받는다. 응답이 없으면(경로 없음·미지원) 경로 안내 중일 때만 직전 값을 쓴다. */
    suspend fun refresh(timeoutMs: Long = 1_500): Destination? {
        val helper = navi?.takeIf { ready } ?: return null
        val answer = CompletableDeferred<Destination?>()
        pending = answer
        if (!request(helper)) return null
        val got = withTimeoutOrNull(timeoutMs) { answer.await() }
        return got ?: _destination.value.takeIf { routeActive == true }
    }

    private fun request(helper: NaviHelper): Boolean = try {
        helper.getDestinationInfo()
        true
    } catch (t: Throwable) {
        Log.i(TAG, "getDestinationInfo 실패: ${t.message}")
        false
    }

    private fun clear() {
        _destination.value = null
        pending?.complete(null)
    }

    private val listener = object : NaviHelperEventListener {
        override fun onDestinationInfo(destinationInfo: DestinationInfo) {
            val remain = destinationInfo.remainTime
            val d = if (remain > 0 && routeActive != false) {
                Destination(remain, destinationInfo.remainDistance.takeIf { it >= 0 })
            } else null
            _destination.value = d
            pending?.complete(d)
        }

        override fun onRouteStarted(routeStartInfo: RouteStartInfo) {
            routeActive = true
            navi?.let(::request)
        }

        override fun onRoutingCompleted() { navi?.let(::request) }
        override fun onReRouteEnded() { navi?.let(::request) }
        override fun onDestinationChanged() { navi?.let(::request) }

        override fun onRouteEnded() { routeActive = false; clear() }
        override fun onRouteCancelled() { routeActive = false; clear() }

        override fun onDestinationArrived(destinationArrivedInfo: DestinationArrivedInfo) {
            Log.i(TAG, "목적지 도착")
            routeActive = false
            clear()
            _arrivals.tryEmit(Unit)
        }

        override fun onNavigationStatus(navigationStatus: NavigationStatus) {
            routeActive = navigationStatus == NavigationStatus.ROUTE_DRIVE
            if (routeActive == false) clear() else navi?.let(::request)
        }
    }

    private companion object {
        const val TAG = "TeumNavi"
    }
}
