package ai.teum.car.data

import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

data class Credentials(val userId: String, val token: String)

/** POST /devices 등록. 토큰을 가진 동안에는 다시 등록하지 않는다. */
class Auth(private val api: ApiClient, private val store: DeviceStore, private val appVersion: String) {
    private val mutex = Mutex()

    suspend fun ensure(): Credentials = mutex.withLock {
        current() ?: register()
    }

    /** 서버가 토큰을 모를 때(401) 한 번 새로 등록한다. 다른 호출이 이미 바꿨으면 그 값을 쓴다. */
    suspend fun renew(staleToken: String): Credentials = mutex.withLock {
        val now = current()
        if (now != null && now.token != staleToken) return@withLock now
        store.clearCredentials()
        register()
    }

    /** 시연용: 새 사용자로 다시 시작(이전 기록과 연결되지 않음). */
    suspend fun reset(): Credentials = mutex.withLock {
        store.clearCredentials()
        register()
    }

    private fun current(): Credentials? {
        val userId = store.userId ?: return null
        val token = store.token ?: return null
        return Credentials(userId, token)
    }

    private suspend fun register(): Credentials = withContext(Dispatchers.IO) {
        val (userId, token) = api.registerDevice(store.installId, appVersion)
        store.saveCredentials(userId, token)
        Log.i(TAG, "기기 등록 완료")
        Credentials(userId, token)
    }

    private companion object {
        const val TAG = "TeumAuth"
    }
}
