package ai.teum.car.vehicle

import android.car.Car
import android.car.drivingstate.CarUxRestrictions
import android.car.drivingstate.CarUxRestrictionsManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * CarUxRestrictions 로 '정차 중에만' 쓸 편집(루틴 순서 편집)을 잠근다.
 * 차량(AAOS)에서는 제한 상태를 받기 전까지 잠근다. 차량이 아닌 기기(폰 에뮬레이터)는 잠그지 않는다.
 */
class DrivingRestrictions(private val context: Context) {
    private val automotive =
        context.packageManager.hasSystemFeature(PackageManager.FEATURE_AUTOMOTIVE)

    private val _editLocked = MutableStateFlow(automotive)
    val editLocked: StateFlow<Boolean> = _editLocked.asStateFlow()

    private var car: Car? = null

    fun start() {
        if (!automotive || car != null || Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return
        try {
            car = Car.createCar(context.applicationContext, null, Car.CAR_WAIT_TIMEOUT_DO_NOT_WAIT) { c, ready ->
                if (!ready) {
                    _editLocked.value = true
                    return@createCar
                }
                val manager = c.getCarManager(Car.CAR_UX_RESTRICTION_SERVICE) as? CarUxRestrictionsManager
                if (manager == null) {
                    _editLocked.value = true
                    return@createCar
                }
                apply(manager.currentCarUxRestrictions)
                manager.registerListener { apply(it) }
            }
        } catch (t: Throwable) { // android.car 라이브러리 없음
            Log.i(TAG, "주행 제한 상태를 받을 수 없어 편집을 잠급니다: ${t.message}")
            _editLocked.value = true
        }
    }

    private fun apply(r: CarUxRestrictions?) {
        _editLocked.value = r?.isRequiresDistractionOptimization ?: true
    }

    private companion object {
        const val TAG = "TeumUx"
    }
}
