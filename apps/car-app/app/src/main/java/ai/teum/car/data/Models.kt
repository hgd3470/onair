package ai.teum.car.data

import org.json.JSONArray
import org.json.JSONObject

/**
 * 재생 큐의 한 항목. 이야기·광고는 GET /recsys/playlist(또는 루틴 plan)의 items[] 이고,
 * 브리핑(kind=briefing)은 서버가 준 문장(text)을 기기 TTS로 만든 음성 파일을 audioUri 로 쓴다.
 * duration_sec 은 서버가 배속으로 나눈 편성상 길이다(브리핑은 예상 길이).
 */
data class PlaylistItem(
    val contentId: String,
    val kind: String,
    val title: String,
    val category: String,
    val durationSec: Double,
    val audioUri: String,
    /** 브리핑: 화면에 보일 부제(숫자 표기). */
    val subtitle: String = "",
    /** 브리핑: 낭독용 문장(숫자 한글 읽기). 화면에는 쓰지 않는다. */
    val text: String = "",
    /** 브리핑: 출처 이름(sources[].name). */
    val sources: List<String> = emptyList(),
    /** 루틴 plan 의 routine_step (weather·economy·traffic·recommend). */
    val routineStep: String? = null,
    /** 골라 듣기 목록이면 POST /selections 의 selection_id. play 이벤트에 싣는다. */
    val selectionId: String? = null,
    /** 브리핑: audioUri 는 무음 자리이고 재생 중에 Pleos TTS 가 text 를 읽는다. */
    val liveTts: Boolean = false,
) {
    val isAd: Boolean get() = kind == KIND_AD
    val isBriefing: Boolean get() = kind == KIND_BRIEFING
    /** 롱폼(두 사람 대담). 카탈로그 밖이라 청취 이벤트·좋아요를 보내지 않는다. */
    val isLongform: Boolean get() = kind == KIND_LONGFORM

    companion object {
        const val KIND_AD = "ad"
        const val KIND_BRIEFING = "briefing"
        const val KIND_LONGFORM = "longform"

        fun fromJson(o: JSONObject): PlaylistItem {
            val step = o.optString("routine_step").ifEmpty { null }
            if (o.optString("kind") == KIND_BRIEFING) {
                return BriefingCard.fromJson(o).toPlaylistItem(step)
            }
            return PlaylistItem(
                contentId = o.getString("content_id"),
                kind = o.optString("kind", "story"),
                title = o.optString("title", ""),
                category = o.optString("category", ""),
                durationSec = o.optDouble("duration_sec", 0.0),
                audioUri = o.getString("audio_uri"),
                routineStep = step,
            )
        }
    }
}

/** POST /quick-menu 의 카드, 루틴 plan 의 kind=briefing 항목. */
data class BriefingCard(
    val id: String,
    val title: String,
    val subtitle: String,
    val text: String,
    val durationSec: Double,
    val available: Boolean,
    val stale: Boolean,
    val sources: List<String>,
) {
    fun toPlaylistItem(routineStep: String? = null) = PlaylistItem(
        contentId = "briefing-$id",
        kind = PlaylistItem.KIND_BRIEFING,
        title = title,
        category = "",
        durationSec = durationSec,
        audioUri = "",
        subtitle = subtitle,
        text = text,
        sources = sources,
        routineStep = routineStep,
    )

    companion object {
        fun fromJson(o: JSONObject) = BriefingCard(
            id = o.getString("id"),
            title = o.optString("title", ""),
            subtitle = o.optString("subtitle", ""),
            text = o.optString("text", ""),
            durationSec = o.optDouble("duration_sec", 0.0),
            available = o.optBoolean("available", false),
            stale = o.optBoolean("stale", false),
            sources = o.optJSONArray("sources").objects().mapNotNull { s -> s.optString("name").ifEmpty { null } },
        )
    }
}

/** GET/PUT /me/routines 의 루틴 하나. steps 는 단계 type 목록(recommend 는 항상 마지막). */
data class Routine(val routineId: String, val name: String, val label: String?, val steps: List<String>) {
    fun toJson(): JSONObject = JSONObject()
        .put("routine_id", routineId)
        .put("name", name)
        .apply { if (label != null) put("label", label) }
        .put("steps", JSONArray(steps.map { JSONObject().put("type", it) }))

    companion object {
        fun fromJson(o: JSONObject) = Routine(
            routineId = o.getString("routine_id"),
            name = o.optString("name", ""),
            label = o.optString("label").ifEmpty { null },
            steps = RoutineSteps.normalize(o.optJSONArray("steps").objects().map { it.optString("type") }),
        )
    }
}

/** 루틴 단계 규칙. 서버(routine/router.py)와 같다: 같은 단계 한 번, recommend 는 마지막. */
object RoutineSteps {
    const val WEATHER = "weather"
    const val ECONOMY = "economy"
    const val TRAFFIC = "traffic"
    const val RECOMMEND = "recommend"
    val all = listOf(WEATHER, ECONOMY, TRAFFIC, RECOMMEND)

    fun title(type: String) = when (type) {
        WEATHER -> "오늘 날씨"
        ECONOMY -> "경제 브리핑"
        TRAFFIC -> "고속도로 교통상황"
        RECOMMEND -> "추천 콘텐츠"
        else -> type
    }

    /** 칩에 쓰는 짧은 이름. */
    fun shortTitle(type: String) = when (type) {
        WEATHER -> "날씨"
        ECONOMY -> "경제"
        TRAFFIC -> "교통"
        RECOMMEND -> "추천"
        else -> type
    }

    /** 모르는 단계·중복을 빼고 recommend 를 맨 뒤로 보낸다. */
    fun normalize(steps: List<String>): List<String> {
        val known = steps.filter { it in all }.distinct()
        return known.filter { it != RECOMMEND } + known.filter { it == RECOMMEND }
    }

    fun missing(steps: List<String>): List<String> = all.filter { it !in steps }

    /** 끌어서 옮기기. recommend 는 움직이지 않고, 다른 단계도 recommend 뒤로 갈 수 없다. */
    fun move(steps: List<String>, from: Int, to: Int): List<String> {
        if (from !in steps.indices || steps[from] == RECOMMEND) return steps
        val movable = steps.filter { it != RECOMMEND }.toMutableList()
        val item = movable.removeAt(from) // recommend 는 마지막이라 앞 단계의 index 는 그대로다
        movable.add(to.coerceIn(0, movable.size), item)
        return normalize(movable + steps.filter { it == RECOMMEND })
    }
}

/** POST /routines/{id}/plan 응답. */
data class RoutinePlan(
    val routineId: String,
    val name: String,
    val budgetSec: Double,
    val steps: List<String>,
    val items: List<PlaylistItem>,
    val skipped: List<SkippedStep>,
) {
    companion object {
        fun fromJson(o: JSONObject): RoutinePlan {
            val items = o.optJSONArray("items").objects()
                .sortedBy { it.optDouble("start_at_sec", 0.0) }
                .map(PlaylistItem::fromJson)
            return RoutinePlan(
                routineId = o.optString("routine_id"),
                name = o.optString("name"),
                budgetSec = o.optDouble("budget_sec", 0.0),
                steps = o.optJSONArray("steps").objects().map { it.optString("type") },
                items = items,
                skipped = o.optJSONArray("skipped").objects().map {
                    SkippedStep(it.optString("type"), it.optString("reason"), it.optString("message").ifEmpty { null })
                },
            )
        }
    }
}

data class SkippedStep(val type: String, val reason: String, val message: String?)

/**
 * 앱이 아는 차량·위치 상황. /quick-menu·/routines/plan 의 POST 본문에만 싣고 저장하지 않는다.
 * lat/lon 은 이미 소수 둘째 자리로 줄인 값이다.
 */
data class Situation(
    val lat: Double? = null,
    val lon: Double? = null,
    val remainSec: Int? = null,
    val remainM: Int? = null,
) {
    fun toJson(): JSONObject = JSONObject().apply {
        lat?.let { put("lat", it) }
        lon?.let { put("lon", it) }
        remainSec?.let { put("remain_sec", it) }
        remainM?.let { put("remain_m", it) }
    }

    companion object {
        /** 위도·경도를 소수 둘째 자리(약 1km)로 반올림한다. */
        fun round2(v: Double): Double = Math.round(v * 100) / 100.0
    }
}

/** GET /history 의 반응 상태만 사용한다. */
data class Reaction(val liked: Boolean, val saved: Boolean)

/** S02 대분류 A–E 와 서버 interests(category) 매핑. */
data class TopicGroup(val id: String, val label: String, val keywords: String, val categories: List<String>)

object Topics {
    private fun range(from: Int, to: Int) = (from..to).map { "C%02d".format(it) }

    val groups = listOf(
        TopicGroup("A", "사람과 일상", "직장, 연애, 가족, 인간관계", range(1, 5)),
        TopicGroup("B", "생활과 취향", "살림, 음식, 소비, 여행, 건강", range(6, 12)),
        TopicGroup("C", "지식과 세상", "과학·AI, 역사, 인문, 사회", range(13, 17)),
        TopicGroup("D", "문화·취미·이동", "영화, 음악, 스포츠, 교통", range(18, 20)),
        TopicGroup("E", "성경과 신앙", "성경 낭독, 묵상", range(21, 21)),
    )

    /** 선택한 대분류를 A→E 순서로 펼친 C 코드 목록. */
    fun codesFor(selected: Set<String>): List<String> =
        groups.filter { it.id in selected }.flatMap { it.categories }

    /** 선택한 대분류를 A→E 순서로 펼친 interests 쿼리 값. */
    fun interestsFor(selected: Set<String>): String = codesFor(selected).joinToString(",")

    fun labelForCategory(category: String): String? =
        groups.firstOrNull { category in it.categories }?.label
}

object Durations {
    val manualMinutes = listOf(15, 30, 60)
    const val DEFAULT_MINUTES = 30
    /** 경로 안내가 없을 때 추천 듣기는 1시간 편성으로 대신한다. */
    const val RECOMMEND_MINUTES = 60
    /** 골라 듣기에서 '도착까지'를 고른 상태(내비 남은 시간으로 편성). */
    const val UNTIL_ARRIVAL = -1

    fun label(minutes: Int) = if (minutes % 60 == 0) "${minutes / 60}시간" else "${minutes}분"

    /** 카드의 "약 N분". 1분 미만도 1분으로 보인다. */
    fun approxMinutes(sec: Double): String = "약 ${Math.round(sec / 60.0).coerceAtLeast(1)}분"
}

object Speeds {
    val options = listOf(0.8f, 1.0f, 1.2f, 1.5f)
    fun label(speed: Float) = "${"%.1f".format(speed)}x"
}

private fun JSONArray?.objects(): List<JSONObject> =
    if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }
