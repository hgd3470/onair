package ai.teum.car.vehicle

import ai.teum.car.data.Situation
import android.content.Context
import android.os.Handler
import android.os.Looper
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import java.time.LocalTime

/**
 * 차량 맥락 (S2-11). 내비 남은 시간, 좌석 탑승, 주행 제한, 위치를 모은다.
 * - 값은 모두 메모리에만 있고 앱 저장소에 쓰지 않는다. 서버에는 요청할 때 보내기만 한다.
 * - SDK가 없거나 값을 못 받으면 null 로 두고 기존 동작으로 돌아간다.
 * - 가족 콘텐츠: 뒷좌석이 탑승으로 바뀌면 주행(도착까지)당 한 번만 묻는다. 아이 여부는 판단하지 않는다.
 */
class VehicleContext(context: Context, private val scope: CoroutineScope) {
    val navi = NaviSource(context)
    private val seats = SeatSource(context)
    private val restrictions = DrivingRestrictions(context)
    private val location = LocationSource(context)

    val destination: StateFlow<Destination?> get() = navi.destination
    val editLocked: StateFlow<Boolean> get() = restrictions.editLocked

    private val _familyPrompt = MutableStateFlow(false)
    /** 화면에 "가족 콘텐츠로 바꿀까요?"를 띄워야 하는지. */
    val familyPrompt: StateFlow<Boolean> = _familyPrompt.asStateFlow()

    private val _family = MutableStateFlow(false)
    /** 이번 주행 동안 가족 콘텐츠 동의. */
    val family: StateFlow<Boolean> = _family.asStateFlow()

    private var askedThisTrip = false
    private var observing = false
    private val main = Handler(Looper.getMainLooper())

    /** 여러 번 불러도 된다(권한을 받은 뒤 다시 부르면 좌석 구독을 시작한다). 메인 스레드에서 연결한다. */
    fun start() = main.post {
        navi.start()
        seats.start()
        restrictions.start()
        location.start()
        if (!observing) {
            observing = true
            scope.launch(Dispatchers.Main) {
                seats.occupied
                    .map { m -> m.any { (k, v) -> v && SeatSource.isRear(k) } }
                    .distinctUntilChanged()
                    .collect { rear -> if (rear) onRearOccupied() }
            }
            scope.launch(Dispatchers.Main) { navi.arrivals.collect { endTrip() } }
        }
    }

    private fun onRearOccupied() {
        if (askedThisTrip) return
        askedThisTrip = true
        _familyPrompt.value = true
    }

    fun answerFamily(yes: Boolean) {
        _familyPrompt.value = false
        _family.value = yes
    }

    /** 도착하면 이번 주행이 끝난 것으로 본다. 다음 주행에서 다시 물을 수 있다. */
    private fun endTrip() {
        askedThisTrip = false
        _familyPrompt.value = false
        _family.value = false
    }

    /** 탑승한 좌석 수. 좌석 값을 하나도 못 받았으면 null(보내지 않음). */
    val passengers: Int?
        get() = seats.occupied.value.takeIf { it.isNotEmpty() }?.count { it.value }

    fun hour(): Int = LocalTime.now().hour

    /** 요청 직전에 남은 시간·거리와 (원하면) 반올림한 위치를 모은다. */
    suspend fun situation(withLocation: Boolean = true): Situation {
        val dest = navi.refresh()
        val latLon = if (withLocation) location.roundedLatLon() else null
        return Situation(latLon?.first, latLon?.second, dest?.remainSec, dest?.remainM)
    }
}
