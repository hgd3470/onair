package ai.teum.car.data

import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

class HttpException(val code: Int, val body: String) : IOException("HTTP $code: ${body.take(200)}")

/** 온에어 REST API. 모든 함수는 블로킹이므로 IO 디스패처에서 호출한다. */
class ApiClient(origin: String, prefix: String) {
    private val origin = origin.trimEnd('/')
    private val apiBase = this.origin + "/" + prefix.trim('/')

    /** audio_uri 가 /api/v1/... 처럼 '/'로 시작하면 호스트 기준으로 붙인다. 접두어를 다시 붙이지 않는다. */
    fun resolveAudioUrl(audioUri: String): String = resolveAudioUrl(origin, apiBase, audioUri)

    fun registerDevice(installId: String, appVersion: String): Pair<String, String> {
        val body = JSONObject()
            .put("install_id", installId)
            .put("app_version", appVersion)
            .put("platform", "pleos-connect")
        val res = JSONObject(request("POST", "/devices", body = body))
        return res.getString("user_id") to res.getString("token")
    }

    /**
     * GET /recsys/playlist. /playlist 와 같은 입력·응답에 차량 맥락을 더한다.
     * minutes 가 없으면 remainSec(내비 남은 시간)으로 편성한다. 값이 없는 항목은 보내지 않는다.
     */
    fun recsysPlaylist(
        userId: String,
        minutes: Int?,
        remainSec: Int?,
        interests: String,
        speed: Float,
        mode: String,
        hour: Int?,
        passengers: Int?,
        family: Boolean,
    ): List<PlaylistItem> {
        val query = linkedMapOf("user_id" to userId)
        if (minutes != null) query["minutes"] = minutes.toString()
        if (minutes == null && remainSec != null) query["remain_sec"] = remainSec.toString()
        query["interests"] = interests
        query["speed"] = speed.toString()
        query["mode"] = mode
        if (hour != null) query["hour"] = hour.toString()
        if (passengers != null) query["passengers"] = passengers.toString()
        if (family) query["family"] = "true"
        val items = JSONObject(request("GET", "/recsys/playlist", query = query)).optJSONArray("items") ?: JSONArray()
        return (0 until items.length()).map { PlaylistItem.fromJson(items.getJSONObject(it)) }
    }

    /** POST /selections. 골라 듣기에서 고른 주제. */
    fun postSelection(token: String, selectionId: String, interests: List<String>, selectedAt: String) {
        val body = JSONObject()
            .put("selection_id", selectionId)
            .put("interests", JSONArray(interests))
            .put("selected_at", selectedAt)
        request("POST", "/selections", body = body, token = token)
    }

    /** POST /quick-menu. 위치는 URL 이 아니라 본문으로 보낸다. */
    fun quickMenu(situation: Situation): List<BriefingCard> {
        val items = JSONObject(request("POST", "/quick-menu", body = situation.toJson())).optJSONArray("items") ?: JSONArray()
        return (0 until items.length()).map { BriefingCard.fromJson(items.getJSONObject(it)) }
    }

    /** GET /longform. 음성까지 끝난 롱폼(두 사람 대담) 회차. subtitle 에 진행자 둘을 넣는다. */
    fun longform(): List<PlaylistItem> {
        val items = JSONObject(request("GET", "/longform")).optJSONArray("items") ?: JSONArray()
        return (0 until items.length()).map {
            val o = items.getJSONObject(it)
            val hosts = o.optJSONArray("hosts") ?: JSONArray()
            PlaylistItem(
                contentId = o.getString("content_id"),
                kind = PlaylistItem.KIND_LONGFORM,
                title = o.optString("title", ""),
                category = "",
                durationSec = o.optDouble("duration_sec", 0.0),
                audioUri = o.getString("audio_uri"),
                subtitle = (0 until hosts.length()).joinToString(" × ") { i -> hosts.getString(i) },
            )
        }
    }

    fun routines(token: String): List<Routine> {
        val items = JSONObject(request("GET", "/me/routines", token = token)).optJSONArray("routines") ?: JSONArray()
        return (0 until items.length()).map { Routine.fromJson(items.getJSONObject(it)) }
    }

    fun putRoutines(token: String, routines: List<Routine>): List<Routine> {
        val body = JSONObject().put("routines", JSONArray(routines.map { it.toJson() }))
        val items = JSONObject(request("PUT", "/me/routines", body = body, token = token)).optJSONArray("routines") ?: JSONArray()
        return (0 until items.length()).map { Routine.fromJson(items.getJSONObject(it)) }
    }

    /** POST /routines/{id}/plan. body 는 Situation + minutes/hour/family/passengers/speed. */
    fun routinePlan(token: String, routineId: String, body: JSONObject): RoutinePlan {
        val path = "/routines/" + URLEncoder.encode(routineId, "UTF-8") + "/plan"
        return RoutinePlan.fromJson(JSONObject(request("POST", path, body = body, token = token)))
    }

    fun history(token: String): Map<String, Reaction> {
        val items = JSONObject(request("GET", "/history", token = token)).optJSONArray("items") ?: JSONArray()
        return (0 until items.length()).associate {
            val o = items.getJSONObject(it)
            o.getString("content_id") to Reaction(o.optBoolean("liked"), o.optBoolean("saved"))
        }
    }

    fun postEvent(token: String, event: JSONObject) {
        request("POST", "/events", body = event, token = token)
    }

    private fun request(
        method: String,
        path: String,
        query: Map<String, String> = emptyMap(),
        body: JSONObject? = null,
        token: String? = null,
    ): String {
        val qs = if (query.isEmpty()) "" else "?" + query.entries.joinToString("&") {
            URLEncoder.encode(it.key, "UTF-8") + "=" + URLEncoder.encode(it.value, "UTF-8")
        }
        val conn = URL(apiBase + path + qs).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = 5_000
            conn.readTimeout = 15_000 // 루틴 plan·브리핑은 외부 정보(날씨·환율)를 함께 받는다
            conn.setRequestProperty("Accept", "application/json")
            if (token != null) conn.setRequestProperty("Authorization", "Bearer $token")
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            if (code !in 200..299) throw HttpException(code, text)
            return text
        } finally {
            conn.disconnect()
        }
    }

    companion object {
        fun resolveAudioUrl(origin: String, apiBase: String, audioUri: String): String = when {
            audioUri.startsWith("http://") || audioUri.startsWith("https://") || audioUri.startsWith("file:") -> audioUri
            audioUri.startsWith("/") -> origin.trimEnd('/') + audioUri
            else -> apiBase.trimEnd('/') + "/" + audioUri
        }
    }
}
