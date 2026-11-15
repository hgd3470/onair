package ai.teum.car.vehicle

import ai.pleos.playground.location.fused.FusedLocation
import ai.pleos.playground.location.fused.listener.FusedLocationListener
import ai.pleos.playground.location.fused.model.LocationData
import ai.teum.car.data.Situation
import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.CancellationSignal
import android.util.Log
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.Executors
import kotlin.coroutines.resume

/**
 * 날씨 브리핑용 현재 위치.
 * 1) Pleos FusedLocation(차량 융합 위치) 2) 지원 안 되면 Android 위치(FUSED·GPS·네트워크).
 * 소수 둘째 자리(약 1km)로 줄인 값만 돌려주고 앱에 저장하지 않는다. 권한·위치가 없으면 null.
 */
class LocationSource(private val context: Context) {
    private val executor = Executors.newSingleThreadExecutor()
    private var pleos: FusedLocation? = null

    /** 메인 스레드에서. 서비스 연결에 시간이 걸려 미리 붙여 둔다. */
    fun start() {
        if (pleos != null || !granted(PLEOS_PERMISSION)) return
        pleos = try {
            FusedLocation(context.applicationContext).also { it.initialize() }
        } catch (t: Throwable) {
            Log.i(TAG, "Pleos FusedLocation 사용 불가: ${t.message}")
            null
        }
    }

    suspend fun roundedLatLon(timeoutMs: Long = 2_000): Pair<Double, Double>? {
        val latLon = fromPleos(timeoutMs) ?: fromAndroid(timeoutMs)
        if (latLon == null) Log.i(TAG, "위치 없음")
        return latLon?.let { (lat, lon) -> Situation.round2(lat) to Situation.round2(lon) }
    }

    private suspend fun fromPleos(timeoutMs: Long): Pair<Double, Double>? {
        val fused = pleos ?: return null
        val supported = runCatching { fused.isSupported() }.getOrDefault(false)
        if (!supported) return null
        val data: LocationData? = withTimeoutOrNull(timeoutMs) {
            suspendCancellableCoroutine { cont ->
                try {
                    fused.getLastKnownLocation(object : FusedLocationListener {
                        override fun onChanged(location: LocationData?) { if (cont.isActive) cont.resume(location) }
                        override fun onError(e: Exception) { if (cont.isActive) cont.resume(null) }
                    })
                } catch (t: Throwable) {
                    if (cont.isActive) cont.resume(null)
                }
            }
        }
        return data?.takeIf { it.latitude != 0.0 || it.longitude != 0.0 }?.let { it.latitude to it.longitude }
    }

    @SuppressLint("MissingPermission")
    private suspend fun fromAndroid(timeoutMs: Long): Pair<Double, Double>? {
        if (!granted(Manifest.permission.ACCESS_COARSE_LOCATION) && !granted(Manifest.permission.ACCESS_FINE_LOCATION)) return null
        val lm = context.getSystemService(LocationManager::class.java) ?: return null
        val providers = buildList {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) add(LocationManager.FUSED_PROVIDER)
            add(LocationManager.GPS_PROVIDER)
            add(LocationManager.NETWORK_PROVIDER)
        }.filter { p -> runCatching { lm.isProviderEnabled(p) }.getOrDefault(false) }
        val loc = try {
            current(lm, providers, timeoutMs) ?: lastKnown(lm, providers)
        } catch (e: SecurityException) {
            null
        } catch (e: IllegalArgumentException) {
            null
        }
        return loc?.let { it.latitude to it.longitude }
    }

    @SuppressLint("MissingPermission")
    private suspend fun current(lm: LocationManager, providers: List<String>, timeoutMs: Long): Location? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return null
        val provider = providers.firstOrNull() ?: return null
        return withTimeoutOrNull(timeoutMs) {
            suspendCancellableCoroutine { cont ->
                val cancel = CancellationSignal()
                cont.invokeOnCancellation { cancel.cancel() }
                lm.getCurrentLocation(provider, cancel, executor) { if (cont.isActive) cont.resume(it) }
            }
        }
    }

    @SuppressLint("MissingPermission")
    private fun lastKnown(lm: LocationManager, providers: List<String>): Location? =
        (providers + LocationManager.PASSIVE_PROVIDER)
            .mapNotNull { runCatching { lm.getLastKnownLocation(it) }.getOrNull() }
            .filter { System.currentTimeMillis() - it.time < MAX_AGE_MS }
            .maxByOrNull { it.time }

    private fun granted(permission: String) =
        context.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED

    private companion object {
        const val TAG = "TeumLocation"
        const val PLEOS_PERMISSION = "pleos.car.permission.FUSED_LOCATION"
        const val MAX_AGE_MS = 30 * 60 * 1000L
    }
}
