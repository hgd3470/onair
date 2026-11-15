package ai.teum.car.data

import android.content.Context
import java.util.UUID

/**
 * 기기 식별자·토큰·seq·배속을 앱 전용 저장소에 보관한다. 재실행 후에도 유지된다.
 * (allowBackup=false 로 백업에도 포함되지 않는다.)
 */
class DeviceStore(context: Context) {
    private val prefs = context.getSharedPreferences("teum_device", Context.MODE_PRIVATE)

    /** 사람·차량과 무관한 무작위 설치 ID. 최초 1회 생성. */
    val installId: String
        @Synchronized get() = prefs.getString(KEY_INSTALL_ID, null)
            ?: UUID.randomUUID().toString().also { prefs.edit().putString(KEY_INSTALL_ID, it).commit() }

    val userId: String? get() = prefs.getString(KEY_USER_ID, null)
    val token: String? get() = prefs.getString(KEY_TOKEN, null)

    fun saveCredentials(userId: String, token: String) {
        prefs.edit().putString(KEY_USER_ID, userId).putString(KEY_TOKEN, token).commit()
    }

    fun clearCredentials() {
        prefs.edit().remove(KEY_USER_ID).remove(KEY_TOKEN).commit()
    }

    /** 기기 전체에서 1씩 증가하는 seq. 디스크에 먼저 기록한 뒤 돌려주므로 재실행해도 되돌아가지 않는다. */
    @Synchronized
    fun nextSeq(): Long {
        val next = prefs.getLong(KEY_SEQ, 0L) + 1
        prefs.edit().putLong(KEY_SEQ, next).commit()
        return next
    }

    var speed: Float
        get() = prefs.getFloat(KEY_SPEED, 1.0f).takeIf { it in Speeds.options } ?: 1.0f
        set(value) {
            prefs.edit().putFloat(KEY_SPEED, value).apply()
        }

    private companion object {
        const val KEY_INSTALL_ID = "install_id"
        const val KEY_USER_ID = "user_id"
        const val KEY_TOKEN = "token"
        const val KEY_SEQ = "seq"
        const val KEY_SPEED = "speed"
    }
}
