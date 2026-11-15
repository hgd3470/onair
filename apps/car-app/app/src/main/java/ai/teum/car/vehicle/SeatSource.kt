package ai.teum.car.vehicle

import ai.pleos.playground.vehicle.Vehicle
import ai.pleos.playground.vehicle.api.Seat
import ai.pleos.playground.vehicle.api.model.area.SeatArea
import ai.pleos.playground.vehicle.listener.SeatOccupancyListener
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * Pleos Vehicle Seat 의 좌석 탑승(Occupied) 상태. 권한 pleos.car.permission.CAR_SEATS.
 * 값은 메모리에만 두고 저장하지 않는다. SDK·권한이 없으면 빈 상태로 남는다(passengers 를 보내지 않음).
 */
class SeatSource(private val context: Context) {
    /** 좌석 이름(ROW_1_LEFT 등) → 탑승 여부. 값을 못 받은 좌석은 없다. */
    private val _occupied = MutableStateFlow<Map<String, Boolean>>(emptyMap())
    val occupied: StateFlow<Map<String, Boolean>> = _occupied.asStateFlow()

    private var vehicle: Vehicle? = null
    private var registered = false
    private var attempts = 0
    private val handler = Handler(Looper.getMainLooper())

    /** 권한을 받은 뒤 다시 불러도 된다. 이미 연결됐으면 아무것도 하지 않는다. */
    fun start() {
        if (registered) return
        attempts = 0
        handler.removeCallbacksAndMessages(null)
        connect()
    }

    private fun connect() {
        try {
            val v = vehicle ?: Vehicle(context.applicationContext).also {
                it.initialize()
                vehicle = it
            }
            val seat = v.getSeat()
            seat.checkSeatOccupancyCapabilities(
                { areas: List<SeatArea> -> register(seat, areas) },
                { e: Exception -> retry("좌석 정보 확인 실패: ${e.message}") },
            )
        } catch (t: Throwable) {
            retry("Vehicle SDK 사용 불가: ${t.message}")
        }
    }

    /** 서비스 연결 직후에는 실패할 수 있어 몇 번만 다시 시도한다. */
    private fun retry(reason: String) {
        if (++attempts > MAX_ATTEMPTS) {
            Log.i(TAG, "$reason (포기)")
            return
        }
        Log.i(TAG, "$reason (다시 시도 $attempts)")
        handler.postDelayed(::connect, RETRY_MS * attempts)
    }

    private fun register(seat: Seat, areas: List<SeatArea>) {
        if (registered) return
        registered = true
        Log.i(TAG, "좌석 ${areas.size}개 확인")
        areas.forEach { area ->
            val key = area.values.joinToString("+") { it.name }
            runCatching {
                seat.getCurrentSeatOccupancy(area, { v: Boolean? -> update(key, v) }, { e: Exception -> Log.i(TAG, "$key 값 없음: ${e.message}") })
                seat.registerSeatOccupancy(area, object : SeatOccupancyListener {
                    override fun onSeatOccupancyUpdated(value: Boolean?) { update(key, value) }
                    override fun onFailed(e: Exception) { Log.i(TAG, "$key 구독 실패: ${e.message}") }
                })
            }.onFailure { Log.i(TAG, "$key 구독 실패: ${it.message}") }
        }
    }

    /**
     * SDK 값 → 탑승 여부.
     * Vehicle SDK 2.3.2 는 원시값 == 1 일 때 true 를 준다. 그런데 Pleos 에뮬레이터·Virtual Console 은
     * Android 표준(VehicleSeatOccupancyState: 1=VACANT, 2=OCCUPIED)으로 값을 낸다
     * (Virtual Console 에서 뒷좌석 Occupied → 원시값 2 → SDK false). 그래서 SDK false 를 탑승으로 본다.
     * 실차에서 반대로 확인되면 SDK_TRUE_MEANS_OCCUPIED 만 바꾸면 된다.
     */
    private fun update(key: String, sdkValue: Boolean?) {
        val occupied = sdkValue?.let { if (SDK_TRUE_MEANS_OCCUPIED) it else !it }
        _occupied.update { if (occupied == null) it - key else it + (key to occupied) }
    }

    companion object {
        private const val TAG = "TeumSeat"
        private const val MAX_ATTEMPTS = 3
        private const val RETRY_MS = 2_000L
        private const val SDK_TRUE_MEANS_OCCUPIED = false

        fun isRear(key: String) = key.split('+').any { it.startsWith("ROW_2") || it.startsWith("ROW_3") }
    }
}
