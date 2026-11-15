package ai.teum.car

import ai.teum.car.data.ApiClient
import ai.teum.car.data.EventFactory
import ai.teum.car.data.EventType
import ai.teum.car.data.PlaylistItem
import ai.teum.car.data.Routine
import ai.teum.car.data.RoutinePlan
import ai.teum.car.data.RoutineSteps
import ai.teum.car.data.Situation
import ai.teum.car.data.SkipReason
import ai.teum.car.data.Topics
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class Sprint2ContractTest {

    @Test
    fun skipCarriesReasonAndPlayCarriesSelection() {
        val skip = EventFactory.build(EventType.SKIP, "s1", 3, "pb", 12.0, 1.0f, skipReason = SkipReason.USER_NEXT)
        assertEquals("user_next", skip.getString("skip_reason"))
        assertFalse(skip.has("selection_id"))

        val play = EventFactory.build(EventType.PLAY, "s1", 4, "pb", 0.0, 1.0f, selectionId = "0b8e2c4e-1c7a-4b0e-9a55-3f0d1f9b2a11")
        assertEquals("0b8e2c4e-1c7a-4b0e-9a55-3f0d1f9b2a11", play.getString("selection_id"))
        assertFalse(play.has("skip_reason"))

        // skip_reason 은 skip 에만, selection_id 는 play 에만
        val pos = EventFactory.build(EventType.POSITION, "s1", 5, "pb", 1.0, 1.0f, skipReason = SkipReason.ARRIVAL, selectionId = "x")
        assertFalse(pos.has("skip_reason"))
        assertFalse(pos.has("selection_id"))
    }

    @Test
    fun situationRoundsToTwoDecimalsAndOmitsMissing() {
        assertEquals(37.57, Situation.round2(37.5665), 1e-9)
        assertEquals(126.98, Situation.round2(126.9780), 1e-9)
        val body = Situation(lat = 37.57, lon = 126.98, remainSec = 1920).toJson()
        assertEquals(37.57, body.getDouble("lat"), 1e-9)
        assertEquals(1920, body.getInt("remain_sec"))
        assertFalse(body.has("remain_m"))
        assertEquals("{}", Situation().toJson().toString())
    }

    @Test
    fun selectionInterestsAreExpandedCodes() {
        assertEquals(listOf("C13", "C14", "C15", "C16", "C17"), Topics.codesFor(setOf("C")))
    }

    @Test
    fun routineStepsKeepRecommendLast() {
        val steps = listOf("weather", "economy", "traffic", "recommend")
        assertEquals(listOf("economy", "weather", "traffic", "recommend"), RoutineSteps.move(steps, 0, 1))
        assertEquals(listOf("economy", "traffic", "weather", "recommend"), RoutineSteps.move(steps, 0, 5))
        assertEquals(steps, RoutineSteps.move(steps, 3, 0)) // recommend 는 움직이지 않음
        assertEquals(listOf("traffic", "weather", "recommend"), RoutineSteps.normalize(listOf("recommend", "traffic", "weather", "traffic")))
        assertEquals(listOf("economy"), RoutineSteps.missing(listOf("weather", "traffic", "recommend")))

        val json = Routine("commute", "출근길 루틴", "평일 오전", steps).toJson()
        assertEquals("recommend", json.getJSONArray("steps").getJSONObject(3).getString("type"))
    }

    @Test
    fun planMixesBriefingsAndStoriesInStartOrder() {
        val plan = RoutinePlan.fromJson(JSONObject(
            """
            {"routine_id":"commute","name":"출근길 루틴","budget_sec":1920,
             "steps":[{"type":"weather"},{"type":"economy"},{"type":"recommend"}],
             "items":[
               {"content_id":"w2-c08-01","kind":"story","title":"할인표를 뗀 컵","category":"C08",
                "audio_uri":"/api/v1/gen/w2-c08-01.v1.mp3","duration_sec":54.3,"start_at_sec":19.6,"routine_step":"recommend"},
               {"id":"economy","kind":"briefing","title":"경제 브리핑","subtitle":"달러 1,359원","text":"달러는 천삼백오십구 원이에요.",
                "duration_sec":19.6,"available":true,"sources":[{"name":"Frankfurter"}],"routine_step":"economy","start_at_sec":0}
             ],
             "skipped":[{"type":"weather","reason":"unavailable","message":"위치 정보를 받지 못해서…"}]}
            """.trimIndent(),
        ))
        assertEquals(listOf("weather", "economy", "recommend"), plan.steps)
        assertEquals(2, plan.items.size)
        val first = plan.items[0]
        assertTrue(first.isBriefing)
        assertEquals("briefing-economy", first.contentId)
        assertEquals("economy", first.routineStep)
        assertEquals("달러 1,359원", first.subtitle)
        assertEquals(listOf("Frankfurter"), first.sources)
        assertEquals(PlaylistItem.KIND_BRIEFING, first.kind)
        assertEquals("recommend", plan.items[1].routineStep)
        assertEquals("weather", plan.skipped.single().type)
    }

    @Test
    fun localBriefingFileIsNotJoinedToServer() {
        val origin = "http://10.0.2.2:8780"
        assertEquals("file:///data/user/0/ai.teum.car/cache/briefing/a.wav",
            ApiClient.resolveAudioUrl(origin, "$origin/api/v1", "file:///data/user/0/ai.teum.car/cache/briefing/a.wav"))
    }
}
