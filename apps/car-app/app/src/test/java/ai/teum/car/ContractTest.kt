package ai.teum.car

import ai.teum.car.data.ApiClient
import ai.teum.car.data.EventFactory
import ai.teum.car.data.EventType
import ai.teum.car.data.Topics
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.OffsetDateTime
import java.time.ZoneOffset

class ContractTest {

    @Test
    fun topicGroupsMapToServerInterests() {
        assertEquals("C01,C02,C03,C04,C05", Topics.interestsFor(setOf("A")))
        assertEquals("C06,C07,C08,C09,C10,C11,C12", Topics.interestsFor(setOf("B")))
        assertEquals("C13,C14,C15,C16,C17", Topics.interestsFor(setOf("C")))
        assertEquals("C18,C19,C20", Topics.interestsFor(setOf("D")))
        assertEquals("C21", Topics.interestsFor(setOf("E")))
        // 선택 순서와 무관하게 A→E 순서
        assertEquals("C18,C19,C20,C21", Topics.interestsFor(setOf("E", "D")))
        assertEquals("", Topics.interestsFor(emptySet()))
        assertEquals("사람과 일상", Topics.labelForCategory("C03"))
    }

    @Test
    fun audioUriIsJoinedToHostNotPrefix() {
        val origin = "http://10.0.2.2:8780"
        val base = "$origin/api/v1"
        assertEquals("$origin/api/v1/gen/a.mp3", ApiClient.resolveAudioUrl(origin, base, "/api/v1/gen/a.mp3"))
        assertEquals("$base/gen/a.mp3", ApiClient.resolveAudioUrl(origin, base, "gen/a.mp3"))
        assertEquals("https://cdn.example/a.mp3", ApiClient.resolveAudioUrl(origin, base, "https://cdn.example/a.mp3"))
    }

    @Test
    fun clientTimestampHasExplicitOffset() {
        val utc = OffsetDateTime.of(2026, 11, 4, 10, 0, 0, 0, ZoneOffset.UTC)
        assertEquals("2026-11-04T10:00:00.000+00:00", EventFactory.nowIso(utc))
        val kst = OffsetDateTime.of(2026, 11, 4, 19, 0, 0, 0, ZoneOffset.ofHours(9))
        assertEquals("2026-11-04T19:00:00.000+09:00", EventFactory.nowIso(kst))
    }

    @Test
    fun playbackEventCarriesPositionRateAndVersion() {
        val e = EventFactory.build(EventType.POSITION, "story-1", 41, "pb-1", 38.234, 1.2f)
        assertEquals("story-1", e.getString("content_id"))
        assertEquals(41L, e.getLong("seq"))
        assertEquals("pb-1", e.getString("playback_id"))
        assertEquals(38.2, e.getDouble("position_sec"), 1e-9)
        assertEquals(1.2, e.getDouble("playback_rate"), 1e-6)
        assertEquals(1, e.getInt("content_version"))
        assertTrue(e.getString("event_id").matches(Regex("[0-9a-f-]{36}")))
    }

    @Test
    fun reactionOmitsPositionAndAdOmitsRateAndVersion() {
        val like = EventFactory.build(EventType.LIKE, "story-1", 1, null, null, 1.0f)
        assertFalse(like.has("position_sec"))
        assertFalse(like.has("playback_rate"))
        assertFalse(like.has("playback_id"))

        val ad = EventFactory.build(EventType.AD_START, "ad-1", 2, "pb-2", 0.0, 1.5f)
        assertEquals(0.0, ad.getDouble("position_sec"), 0.0)
        assertFalse(ad.has("playback_rate"))
        assertFalse(ad.has("content_version"))
    }
}
