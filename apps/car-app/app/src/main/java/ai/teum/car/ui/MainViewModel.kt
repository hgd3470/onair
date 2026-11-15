package ai.teum.car.ui

import ai.teum.car.TeumApp
import ai.teum.car.data.BriefingCard
import ai.teum.car.data.Credentials
import ai.teum.car.data.Durations
import ai.teum.car.data.EventFactory
import ai.teum.car.data.EventType
import ai.teum.car.data.HttpException
import ai.teum.car.data.PlaylistItem
import ai.teum.car.data.Reaction
import ai.teum.car.data.Routine
import ai.teum.car.data.RoutinePlan
import ai.teum.car.data.RoutineSteps
import ai.teum.car.data.Topics
import ai.teum.car.playback.MediaItems
import ai.teum.car.playback.PlaybackService
import android.app.Application
import android.content.ComponentName
import android.os.SystemClock
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.guava.await
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONException
import java.io.IOException
import java.util.UUID

enum class Screen { Home, Pick, Loading, Player, LoadError, Routine, Deep }

enum class ListenMode(val label: String) {
    Recommend("추천 듣기"), Pick("골라 듣기"), Briefing("브리핑"), Routine("루틴"), Longform("깊이 듣기"),
}

enum class LoadProblem { Network, Empty, Voice }

data class QuickMenuUi(
    val cards: List<BriefingCard> = emptyList(),
    val loading: Boolean = false,
    val failed: Boolean = false,
)

/** 루틴 화면 상태. plan 은 '루틴 시작' 뒤에만 있다. */
data class RoutineUi(
    val plan: RoutinePlan? = null,
    /** 서버는 줬지만 기기 TTS로 음성을 못 만든 단계. */
    val voiceFailed: Set<String> = emptySet(),
    val editing: Boolean = false,
    val saving: Boolean = false,
)

data class UiState(
    val screen: Screen = Screen.Home,
    val selectedGroups: Set<String> = emptySet(),
    val minutes: Int = Durations.DEFAULT_MINUTES,
    val mode: ListenMode = ListenMode.Recommend,
    val loadProblem: LoadProblem? = null,
    val loadingText: String = "이야기 목록을 불러오고 있어요",
    val queue: List<PlaylistItem> = emptyList(),
    val index: Int = 0,
    val isPlaying: Boolean = false,
    val isBuffering: Boolean = false,
    val ended: Boolean = false,
    val positionMs: Long = 0,
    val durationMs: Long = 0,
    val speed: Float = 1.0f,
    val reactions: Map<String, Reaction> = emptyMap(),
    val pendingReactions: Set<String> = emptySet(),
    val playerError: String? = null,
    val message: String? = null,
    // ---- 2차 스프린트 ----
    val quickMenu: QuickMenuUi = QuickMenuUi(),
    val routines: List<Routine> = emptyList(),
    val routine: RoutineUi = RoutineUi(),
    /** 경로 안내 중이면 목적지까지 남은 분. */
    val destinationMin: Int? = null,
    /** 주행 중(CarUxRestrictions) — 순서 편집을 막는다. */
    val editLocked: Boolean = false,
    val familyPrompt: Boolean = false,
    val family: Boolean = false,
    /** 도착 뒤: 듣던 편까지만 재생하고 끝낸다. */
    val arrived: Boolean = false,
    /** 홈 '롱폼' 줄: 음성까지 끝난 두 사람 대담 회차. */
    val longform: List<PlaylistItem> = emptyList(),
    val longformLoading: Boolean = true,
) {
    val current: PlaylistItem? get() = queue.getOrNull(index)
    val upNext: List<PlaylistItem> get() = if (queue.isEmpty()) emptyList() else queue.drop(index + 1).take(5)
    val hasPrevious: Boolean get() = index > 0
    val hasNext: Boolean get() = index < queue.size - 1
    val hasSession: Boolean get() = queue.isNotEmpty()
    val activeRoutine: Routine? get() = routines.firstOrNull()
}

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val app = TeumApp.from(application)
    private val vehicle = app.vehicle
    private val _state = MutableStateFlow(UiState(speed = app.store.speed))
    val state: StateFlow<UiState> = _state.asStateFlow()

    private var controller: MediaController? = null
    private var loadJob: Job? = null
    private var lastRequest: (() -> Unit)? = null
    private var quickMenuAt = 0L

    init {
        // 최초 실행 시 기기 등록(재생과 무관). 실패해도 편성 요청 때 다시 시도한다.
        viewModelScope.launch {
            runCatching { app.auth.ensure() }.onFailure { Log.w(TAG, "기기 등록 지연: ${it.message}") }
            loadRoutines()
        }
        viewModelScope.launch { connectController() }
        viewModelScope.launch {
            while (isActive) {
                syncPosition()
                delay(500)
            }
        }
        observeVehicle()
        refreshQuickMenu(force = true)
        refreshLongform()
    }

    private fun observeVehicle() {
        vehicle.start()
        viewModelScope.launch {
            vehicle.destination.collect { d ->
                val routeChanged = (d == null) != (_state.value.destinationMin == null)
                _state.update { it.copy(destinationMin = d?.remainMinutes) }
                // 경로 안내가 시작·종료되면 교통 카드가 바뀌므로 퀵 메뉴를 다시 받는다.
                if (routeChanged) refreshQuickMenu(force = true)
            }
        }
        viewModelScope.launch {
            vehicle.editLocked.collect { locked ->
                // 편집 중 주행 제한으로 바뀌면 편집을 닫는다(바꾼 순서는 이미 저장됨).
                _state.update { s -> s.copy(editLocked = locked, routine = if (locked) s.routine.copy(editing = false) else s.routine) }
            }
        }
        viewModelScope.launch { vehicle.familyPrompt.collect { p -> _state.update { it.copy(familyPrompt = p) } } }
        viewModelScope.launch { vehicle.family.collect { f -> _state.update { it.copy(family = f) } } }
        viewModelScope.launch {
            vehicle.navi.arrivals.collect {
                if (_state.value.hasSession) {
                    _state.update { it.copy(arrived = true, message = "목적지에 도착했어요. 듣던 편까지만 들려 드릴게요.") }
                }
            }
        }
        // 홈의 "목적지까지 N분"을 따라가도록 가끔 다시 묻는다.
        viewModelScope.launch {
            while (isActive) {
                delay(DESTINATION_REFRESH_MS)
                if (_state.value.screen == Screen.Home || _state.value.screen == Screen.Routine) vehicle.navi.refresh()
            }
        }
    }

    /** 권한을 새로 받은 뒤 차량 SDK·홈 정보를 다시 읽는다. */
    fun onPermissionsChanged() {
        vehicle.start()
        refreshQuickMenu(force = true)
    }

    // ---- S01 홈 / S02 ----

    fun openPick() = _state.update { it.copy(screen = Screen.Pick) }

    /** 깊이 듣기(두 사람 팟캐스트) 회차 화면. 목록이 비었으면 다시 받아 온다. */
    fun openDeep() {
        _state.update { it.copy(screen = Screen.Deep) }
        if (_state.value.longform.isEmpty()) refreshLongform()
    }

    fun goHome() {
        cancelLoad()
        _state.update { it.copy(screen = Screen.Home, loadProblem = null, routine = it.routine.copy(editing = false)) }
        refreshQuickMenu(force = false)
    }

    fun openPlayer() {
        val s = _state.value
        if (!s.hasSession) return
        _state.update { it.copy(screen = if (s.mode == ListenMode.Routine) Screen.Routine else Screen.Player) }
    }

    fun toggleGroup(id: String) = _state.update {
        it.copy(selectedGroups = if (id in it.selectedGroups) it.selectedGroups - id else it.selectedGroups + id)
    }

    fun selectMinutes(minutes: Int) = _state.update { it.copy(minutes = minutes) }

    fun answerFamily(yes: Boolean) {
        vehicle.answerFamily(yes)
        if (yes) _state.update { it.copy(message = "이번 주행 동안 가족 콘텐츠를 먼저 들려 드릴게요.") }
    }

    fun startRecommend() {
        lastRequest = ::startRecommend
        launchQueue(ListenMode.Recommend, Screen.Player, "이야기 목록을 불러오고 있어요") {
            val dest = vehicle.navi.refresh()
            val items = withAuth { creds ->
                app.api.recsysPlaylist(
                    creds.userId,
                    minutes = if (dest == null) Durations.RECOMMEND_MINUTES else null,
                    remainSec = dest?.remainSec,
                    interests = "", speed = _state.value.speed, mode = "recommend",
                    hour = vehicle.hour(), passengers = vehicle.passengers, family = vehicle.family.value,
                )
            }
            Prepared(items)
        }
    }

    fun startPick() {
        val s = _state.value
        if (s.selectedGroups.isEmpty()) return // 0개 재검증
        val codes = Topics.codesFor(s.selectedGroups)
        val untilArrival = s.minutes == Durations.UNTIL_ARRIVAL
        val minutes = if (untilArrival) null else s.minutes
        lastRequest = ::startPick
        launchQueue(ListenMode.Pick, Screen.Player, "이야기 목록을 불러오고 있어요") {
            val dest = if (untilArrival) vehicle.navi.refresh() else null
            val selectionId = UUID.randomUUID().toString()
            val selectedAt = EventFactory.nowIso()
            val items = withAuth { creds ->
                app.api.recsysPlaylist(
                    creds.userId,
                    minutes = minutes ?: if (dest == null) Durations.DEFAULT_MINUTES else null,
                    remainSec = dest?.remainSec,
                    interests = codes.joinToString(","), speed = _state.value.speed, mode = "pick",
                    hour = vehicle.hour(), passengers = vehicle.passengers, family = vehicle.family.value,
                )
            }
            // 고른 주제 기록. 같은 selection_id 의 play 가 와야 서버 선호에 반영된다(순서 무관).
            postSelection(selectionId, codes, selectedAt)
            Prepared(items.map { if (it.isAd) it else it.copy(selectionId = selectionId) })
        }
    }

    private fun postSelection(selectionId: String, codes: List<String>, selectedAt: String) {
        app.appScope.launch(Dispatchers.IO) {
            runCatching { withAuth { app.api.postSelection(it.token, selectionId, codes, selectedAt) } }
                .onFailure { Log.w(TAG, "selections 전송 실패: ${it.message}") }
        }
    }

    // ---- 퀵 메뉴 · 브리핑 ----

    fun refreshQuickMenu(force: Boolean) {
        val now = SystemClock.elapsedRealtime()
        if (_state.value.quickMenu.loading) return
        if (!force && now - quickMenuAt < QUICK_MENU_TTL_MS && _state.value.quickMenu.cards.isNotEmpty()) return
        quickMenuAt = now
        _state.update { it.copy(quickMenu = it.quickMenu.copy(loading = true)) }
        viewModelScope.launch {
            val cards = runCatching {
                val situation = vehicle.situation(withLocation = true)
                withContext(Dispatchers.IO) { app.api.quickMenu(situation) }
            }.onFailure { Log.w(TAG, "퀵 메뉴 실패: ${it.message}") }.getOrNull()
            _state.update {
                it.copy(quickMenu = QuickMenuUi(cards = cards ?: it.quickMenu.cards, loading = false, failed = cards == null))
            }
        }
    }

    fun playBriefing(card: BriefingCard) {
        if (!card.available) return
        lastRequest = { playBriefing(card) }
        launchQueue(ListenMode.Briefing, Screen.Player, "브리핑을 준비하고 있어요") {
            val (items, failed) = voiced(listOf(card.toPlaylistItem()))
            if (failed.isNotEmpty()) throw VoiceException()
            Prepared(items)
        }
    }

    // ---- 깊이 듣기(롱폼, 두 사람 팟캐스트) ----

    fun refreshLongform() {
        _state.update { it.copy(longformLoading = true) }
        viewModelScope.launch {
            val items = runCatching { withContext(Dispatchers.IO) { app.api.longform() } }
                .onFailure { Log.w(TAG, "롱폼 목록 실패: ${it.message}") }.getOrNull()
            _state.update { it.copy(longform = items ?: it.longform, longformLoading = false) }
        }
    }

    /** 고른 회차부터 듣고, 나머지 회차가 '다음 이야기'로 이어진다. */
    fun playLongform(item: PlaylistItem) {
        lastRequest = { playLongform(item) }
        launchQueue(ListenMode.Longform, Screen.Player, "롱폼을 불러오고 있어요") {
            Prepared(listOf(item) + _state.value.longform.filter { it.contentId != item.contentId })
        }
    }

    // ---- 루틴 ----

    private suspend fun loadRoutines() {
        val list = runCatching { withContext(Dispatchers.IO) { withAuth { app.api.routines(it.token) } } }
            .onFailure { Log.w(TAG, "루틴 불러오기 실패: ${it.message}") }.getOrNull() ?: return
        _state.update { it.copy(routines = list) }
    }

    fun openRoutineEditor() {
        val s = _state.value
        if (s.editLocked) {
            _state.update { it.copy(message = "순서 편집은 정차 중에만 할 수 있어요") }
            return
        }
        if (s.activeRoutine == null) {
            viewModelScope.launch { loadRoutines() }
            return
        }
        // 다른 듣기가 재생 중이면 루틴 화면은 편집만 보여 준다(plan 없음).
        val routineUi = if (s.mode == ListenMode.Routine && s.hasSession) s.routine else RoutineUi()
        _state.update { it.copy(screen = Screen.Routine, routine = routineUi.copy(editing = true)) }
    }

    fun closeRoutineEditor() = _state.update { it.copy(routine = it.routine.copy(editing = false)) }

    fun startRoutine() {
        val routine = _state.value.activeRoutine ?: run {
            viewModelScope.launch { loadRoutines() }
            _state.update { it.copy(message = "루틴을 불러오지 못했어요. 잠시 뒤 다시 눌러주세요.") }
            return
        }
        lastRequest = ::startRoutine
        launchQueue(ListenMode.Routine, Screen.Routine, "루틴을 준비하고 있어요") {
            val situation = vehicle.situation(withLocation = true)
            val body = situation.toJson()
            if (situation.remainSec == null) body.put("minutes", Durations.DEFAULT_MINUTES)
            body.put("hour", vehicle.hour())
            body.put("family", vehicle.family.value)
            vehicle.passengers?.let { body.put("passengers", it) }
            body.put("speed", _state.value.speed.toDouble())
            val plan = withAuth { app.api.routinePlan(it.token, routine.routineId, body) }
            val (items, failed) = voiced(plan.items)
            Prepared(items, plan, failed)
        }
    }

    /** 끌어서 놓았을 때. recommend 는 움직이지 않는다. */
    fun moveStep(from: Int, to: Int) {
        val r = _state.value.activeRoutine ?: return
        val steps = RoutineSteps.move(r.steps, from, to)
        if (steps != r.steps) saveRoutine(r.copy(steps = steps))
    }

    fun addStep(type: String) {
        val r = _state.value.activeRoutine ?: return
        if (type in r.steps) return
        saveRoutine(r.copy(steps = RoutineSteps.normalize(r.steps + type)))
    }

    fun removeStep(type: String) {
        val r = _state.value.activeRoutine ?: return
        if (r.steps.size <= 1) return
        saveRoutine(r.copy(steps = r.steps - type))
    }

    /** PUT /me/routines. 실패하면 되돌린다. */
    private fun saveRoutine(updated: Routine) {
        val s = _state.value
        if (s.editLocked) {
            _state.update { it.copy(message = "순서 편집은 정차 중에만 할 수 있어요") }
            return
        }
        val before = s.routines
        val next = before.map { if (it.routineId == updated.routineId) updated else it }
        _state.update { it.copy(routines = next, routine = it.routine.copy(saving = true)) }
        viewModelScope.launch {
            val saved = runCatching { withContext(Dispatchers.IO) { withAuth { app.api.putRoutines(it.token, next) } } }
                .onFailure { Log.w(TAG, "루틴 저장 실패: ${it.message}") }.getOrNull()
            _state.update {
                it.copy(
                    routines = saved ?: before,
                    routine = it.routine.copy(saving = false),
                    message = if (saved == null) "순서를 저장하지 못했어요. 다시 해 주세요." else it.message,
                )
            }
        }
    }

    // ---- 공통: 목록 받기 → (브리핑 음성 만들기) → 재생 ----

    private class Prepared(
        val items: List<PlaylistItem>,
        val plan: RoutinePlan? = null,
        val voiceFailed: Set<String> = emptySet(),
    )

    private class VoiceException : IOException("voice")

    fun retryLoad() {
        lastRequest?.invoke()
    }

    fun cancelLoad() {
        loadJob?.cancel()
        loadJob = null
    }

    /** 시연용: 들을 이야기가 없을 때 새 사용자로 다시 등록하고 같은 요청을 반복한다. */
    fun resetUserAndRetry() {
        cancelLoad()
        loadJob = viewModelScope.launch {
            _state.update { it.copy(screen = Screen.Loading, loadProblem = null) }
            runCatching { app.auth.reset() }
            loadRoutines()
            lastRequest?.invoke()
        }
    }

    private fun launchQueue(mode: ListenMode, target: Screen, loadingText: String, build: suspend () -> Prepared) {
        cancelLoad()
        val speed = _state.value.speed
        _state.update { it.copy(screen = Screen.Loading, loadProblem = null, loadingText = loadingText) }
        loadJob = viewModelScope.launch {
            val prepared = try {
                build()
            } catch (e: CancellationException) {
                throw e
            } catch (e: VoiceException) {
                fail(LoadProblem.Voice)
                return@launch
            } catch (e: IOException) {
                Log.w(TAG, "목록 실패: ${e.message}")
                fail(LoadProblem.Network)
                return@launch
            } catch (e: JSONException) {
                Log.w(TAG, "목록 응답 오류: ${e.message}")
                fail(LoadProblem.Network)
                return@launch
            }
            if (!isActive) return@launch // 취소된 요청의 늦은 응답으로 재생하지 않는다
            val items = prepared.items
            if (items.isEmpty()) {
                fail(if (prepared.voiceFailed.isNotEmpty() && prepared.plan?.items?.isNotEmpty() == true) LoadProblem.Voice else LoadProblem.Empty)
                return@launch
            }
            val c = controller ?: connectController()
            if (c == null) {
                fail(LoadProblem.Network)
                return@launch
            }
            app.voice.clearExcept(items.filter { it.isBriefing }.map { it.audioUri })
            _state.update {
                it.copy(
                    screen = target, mode = mode, queue = items, index = 0, arrived = false,
                    positionMs = 0, durationMs = 0, playerError = null, ended = false,
                    routine = RoutineUi(plan = prepared.plan, voiceFailed = prepared.voiceFailed),
                )
            }
            // 사용자가 눌렀으므로 재생을 시작한다(앱 시작·연결 시 자동 재생 없음).
            c.setMediaItems(items.map { MediaItems.toMediaItem(it, app.api.resolveAudioUrl(it.audioUri)) })
            c.setPlaybackSpeed(speed)
            c.prepare()
            c.play()
            refreshHistory()
        }
    }

    private fun fail(problem: LoadProblem) {
        _state.update { it.copy(screen = Screen.LoadError, loadProblem = problem) }
    }

    /** 브리핑 항목을 기기 TTS 음성 파일로 바꾼다. 못 만든 단계는 빼고 이름을 돌려준다. */
    private suspend fun voiced(items: List<PlaylistItem>): Pair<List<PlaylistItem>, Set<String>> {
        val failed = mutableSetOf<String>()
        val out = items.mapNotNull { item ->
            if (!item.isBriefing) return@mapNotNull item
            try {
                app.voice.prepare(item, _state.value.speed)
            } catch (e: IOException) {
                Log.w(TAG, "브리핑 음성 실패 ${item.contentId}: ${e.message}")
                failed += item.routineStep ?: item.contentId
                null
            }
        }
        return out to failed
    }

    /** 토큰이 필요한 호출. 서버가 토큰을 모르면(401) 한 번 새로 등록해 다시 부른다. */
    private suspend fun <T> withAuth(block: (Credentials) -> T): T = withContext(Dispatchers.IO) {
        val creds = app.auth.ensure()
        try {
            block(creds)
        } catch (e: HttpException) {
            if (e.code != 401) throw e
            block(app.auth.renew(creds.token))
        }
    }

    // ---- S04 ----

    fun togglePlay() {
        val c = controller ?: return
        when {
            c.playerError != null -> retryPlayback()
            c.playbackState == Player.STATE_ENDED -> { c.seekTo(0); c.play() }
            c.playWhenReady -> c.pause()
            else -> { if (c.playbackState == Player.STATE_IDLE) c.prepare(); c.play() }
        }
    }

    fun next() {
        val s = _state.value
        if (s.current?.isAd == true && s.playerError == null) return
        controller?.run {
            seekToNextMediaItem()
            if (playerError != null) prepare()
        }
    }

    fun previous() {
        val s = _state.value
        if (s.current?.isAd == true) return
        controller?.seekToPreviousMediaItem()
    }

    fun retryPlayback() {
        controller?.run { prepare(); play() }
    }

    fun setSpeed(speed: Float) {
        app.store.speed = speed
        controller?.setPlaybackSpeed(speed)
        _state.update { it.copy(speed = speed) }
    }

    fun toggleLike() = toggleReaction(liked = true)
    fun toggleSave() = toggleReaction(liked = false)

    private fun toggleReaction(liked: Boolean) {
        val item = _state.value.current?.takeIf { !it.isAd && !it.isBriefing && !it.isLongform } ?: return
        val key = item.contentId + if (liked) "#like" else "#save"
        if (key in _state.value.pendingReactions) return
        val before = _state.value.reactions[item.contentId] ?: Reaction(liked = false, saved = false)
        val after = if (liked) before.copy(liked = !before.liked) else before.copy(saved = !before.saved)
        val type = if (liked) {
            if (after.liked) EventType.LIKE else EventType.UNLIKE
        } else {
            if (after.saved) EventType.SAVE else EventType.UNSAVE
        }
        _state.update {
            it.copy(reactions = it.reactions + (item.contentId to after), pendingReactions = it.pendingReactions + key)
        }
        viewModelScope.launch {
            val ok = app.events.reaction(type, item.contentId, app.playbackIdFor(item.contentId))
            _state.update { s ->
                val reverted = if (ok) s.reactions else {
                    val cur = s.reactions[item.contentId] ?: after
                    s.reactions + (item.contentId to if (liked) cur.copy(liked = before.liked) else cur.copy(saved = before.saved))
                }
                s.copy(
                    reactions = reverted,
                    pendingReactions = s.pendingReactions - key,
                    message = if (ok) null else "서버에 반영하지 못했어요. 다시 눌러주세요.",
                )
            }
        }
    }

    fun consumeMessage() = _state.update { it.copy(message = null) }

    private fun refreshHistory() {
        viewModelScope.launch {
            val map = runCatching { withAuth { app.api.history(it.token) } }.getOrNull() ?: return@launch
            _state.update { s ->
                // 전송 중인 토글은 덮어쓰지 않는다.
                val pendingIds = s.pendingReactions.map { it.substringBefore('#') }.toSet()
                s.copy(reactions = s.reactions + map.filterKeys { it !in pendingIds })
            }
        }
    }

    // ---- MediaController ----

    private suspend fun connectController(): MediaController? {
        controller?.let { return it }
        return try {
            val ctx = getApplication<Application>()
            val token = SessionToken(ctx, ComponentName(ctx, PlaybackService::class.java))
            val c = MediaController.Builder(ctx, token).buildAsync().await()
            controller = c
            c.addListener(listener)
            restoreFromController(c)
            c
        } catch (e: Exception) {
            Log.w(TAG, "미디어 세션 연결 실패: ${e.message}")
            null
        }
    }

    /** 화면이 다시 만들어졌을 때 재생 중인 큐를 다시 보여 준다(재생 상태는 건드리지 않음). */
    private fun restoreFromController(c: MediaController) {
        if (c.mediaItemCount == 0 || _state.value.hasSession) return
        val items = (0 until c.mediaItemCount).map { MediaItems.toPlaylistItem(c.getMediaItemAt(it)) }
        val steps = items.mapNotNull { it.routineStep }.distinct()
        val mode = when {
            steps.isNotEmpty() -> ListenMode.Routine
            items.all { it.isBriefing } -> ListenMode.Briefing
            items.all { it.isLongform } -> ListenMode.Longform
            items.any { it.selectionId != null } -> ListenMode.Pick
            else -> ListenMode.Recommend
        }
        val plan = if (steps.isEmpty()) null else RoutinePlan("", "", 0.0, steps, items, emptyList())
        _state.update {
            it.copy(
                queue = items, index = c.currentMediaItemIndex, mode = mode, routine = RoutineUi(plan = plan),
                screen = if (mode == ListenMode.Routine) Screen.Routine else Screen.Player,
            )
        }
        syncFromController(c)
        refreshHistory()
    }

    private val listener = object : Player.Listener {
        override fun onEvents(player: Player, events: Player.Events) {
            controller?.let(::syncFromController)
        }

        override fun onTimelineChanged(timeline: androidx.media3.common.Timeline, reason: Int) {
            // 도착으로 뒤 항목이 지워지면 화면 목록도 줄인다.
            val c = controller ?: return
            if (c.mediaItemCount in 1 until _state.value.queue.size) {
                _state.update { s -> s.copy(queue = s.queue.take(c.mediaItemCount)) }
            }
        }

        override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
            _state.update { it.copy(playerError = null) }
        }

        override fun onPlayerError(error: PlaybackException) {
            Log.w(TAG, "재생 오류: ${error.errorCodeName} ${error.message}")
            _state.update { it.copy(playerError = errorText(it.current)) }
        }
    }

    private fun errorText(item: PlaylistItem?) =
        if (item?.isBriefing == true) "브리핑을 재생하지 못했어요" else "이야기를 불러오지 못했어요"

    private fun syncFromController(c: MediaController) {
        _state.update {
            it.copy(
                index = c.currentMediaItemIndex.coerceAtLeast(0),
                isPlaying = c.playWhenReady && c.playbackState != Player.STATE_ENDED,
                isBuffering = c.playbackState == Player.STATE_BUFFERING,
                ended = c.playbackState == Player.STATE_ENDED,
                positionMs = c.currentPosition,
                durationMs = c.duration.takeIf { d -> d != C.TIME_UNSET && d > 0 }
                    ?: ((it.current?.durationSec ?: 0.0) * it.speed * 1000).toLong(),
                playerError = if (c.playerError == null) null else it.playerError ?: errorText(it.current),
            )
        }
    }

    private fun syncPosition() {
        val c = controller ?: return
        val screen = _state.value.screen
        if (screen == Screen.Player || screen == Screen.Routine) syncFromController(c)
    }

    override fun onCleared() {
        controller?.removeListener(listener)
        controller?.release()
        controller = null
    }

    private companion object {
        const val TAG = "TeumVM"
        const val QUICK_MENU_TTL_MS = 3 * 60 * 1000L
        const val DESTINATION_REFRESH_MS = 60 * 1000L
    }
}
