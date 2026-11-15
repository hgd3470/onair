package ai.teum.car.ui

import ai.teum.car.data.Durations
import ai.teum.car.data.PlaylistItem
import ai.teum.car.data.RoutineSteps
import ai.teum.car.data.Topics
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import kotlin.math.roundToInt

// ---------------- 루틴 (발표 슬라이드 9쪽 (b)) ----------------

/** 단계 카드 한 줄에 보일 내용. */
private data class StepRow(
    val type: String,
    val title: String,
    val subtitle: String,
    val duration: String?,
    val missing: Boolean,
)

@Composable
fun RoutineScreen(state: UiState, vm: MainViewModel) {
    val routine = state.activeRoutine
    val plan = state.routine.plan
    val current = state.current?.takeIf { plan != null }
    val playingStep = current?.routineStep
    Column(Modifier.fillMaxSize().background(TeumColors.Background)) {
        // 상단: ← 출근길 루틴 / 평일 오전 · 목적지까지 32분 / 2 / 4 재생 중
        Row(
            Modifier.fillMaxWidth().padding(start = 24.dp, top = 16.dp, end = 32.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            CircleIconButton(TeumIcons.Back, "홈", onClick = vm::goHome)
            Spacer(Modifier.width(20.dp))
            Column(Modifier.weight(1f)) {
                Text(routine?.name ?: plan?.name ?: "루틴", fontSize = 30.sp, fontWeight = FontWeight.Bold, color = TeumColors.Text)
                Text(routineSubtitle(routine?.label, state.destinationMin), fontSize = 20.sp, color = TeumColors.SubText)
            }
            if (plan != null && playingStep != null && !state.ended) {
                val n = plan.steps.indexOf(playingStep) + 1
                Text(
                    "$n / ${plan.steps.size} 재생 중", fontSize = 22.sp, fontWeight = FontWeight.SemiBold, color = TeumColors.Primary,
                    modifier = Modifier.clip(RoundedCornerShape(24.dp)).background(TeumColors.AccentSurface)
                        .padding(horizontal = 20.dp, vertical = 10.dp),
                )
            }
        }
        BoxWithConstraints(Modifier.fillMaxSize()) {
            if (maxWidth > maxHeight) {
                Row(Modifier.fillMaxSize().padding(24.dp), horizontalArrangement = Arrangement.spacedBy(28.dp)) {
                    Column(Modifier.weight(1f).fillMaxHeight().verticalScroll(rememberScrollState())) { StepList(state, vm) }
                    Column(Modifier.weight(1f).fillMaxHeight().verticalScroll(rememberScrollState())) { PlayPanel(state, vm) }
                }
            } else {
                Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp)) {
                    PlayPanel(state, vm)
                    Spacer(Modifier.height(28.dp))
                    StepList(state, vm)
                }
            }
        }
    }
}

// ---- 왼쪽: 듣는 순서 ----

@Composable
private fun StepList(state: UiState, vm: MainViewModel) {
    val routine = state.activeRoutine ?: return
    val ui = state.routine
    val editing = ui.editing && !state.editLocked
    // 편집 중이면 저장된 순서, 재생 중이면 이번 plan 순서를 보인다.
    val types = if (editing || ui.plan == null) routine.steps else ui.plan.steps
    val rows = types.map { stepRow(it, state) }
    val playingStep = state.current?.routineStep?.takeIf { ui.plan != null && !state.ended }

    Row(verticalAlignment = Alignment.CenterVertically) {
        SectionTitle("듣는 순서")
        Spacer(Modifier.weight(1f))
        if (editing) {
            PrimaryAction("완료", null, Modifier.width(140.dp), onClick = vm::closeRoutineEditor)
        } else {
            SecondaryAction(
                "순서 편집", icon = if (state.editLocked) TeumIcons.Lock else TeumIcons.Edit,
                dimmed = state.editLocked, onClick = vm::openRoutineEditor,
            )
        }
    }
    Spacer(Modifier.height(8.dp))
    Text(
        when {
            state.editLocked -> "순서 편집은 정차 중에만 할 수 있어요"
            editing && ui.plan != null -> "끌어서 순서를 바꿔요 · 다음 루틴부터 적용돼요"
            editing -> "끌어서 순서를 바꿔요 · 추천은 항상 마지막이에요"
            else -> ""
        },
        fontSize = 18.sp, color = TeumColors.SubText,
    )
    Spacer(Modifier.height(12.dp))

    val density = LocalDensity.current
    val slotPx = with(density) { (STEP_HEIGHT + STEP_GAP).dp.toPx() }
    var dragIndex by remember { mutableStateOf<Int?>(null) }
    var dragOffset by remember { mutableFloatStateOf(0f) }
    val movable = rows.count { it.type != RoutineSteps.RECOMMEND }

    Column(verticalArrangement = Arrangement.spacedBy(STEP_GAP.dp)) {
        rows.forEachIndexed { i, row ->
            val canDrag = editing && row.type != RoutineSteps.RECOMMEND && movable > 1
            val dragging = dragIndex == i
            val dragModifier = if (!canDrag) Modifier else Modifier.pointerInput(types) {
                detectDragGestures(
                    onDragStart = { dragIndex = i; dragOffset = 0f },
                    onDrag = { change, amount -> change.consume(); dragOffset += amount.y },
                    onDragEnd = {
                        val to = (i + (dragOffset / slotPx).roundToInt()).coerceIn(0, movable - 1)
                        dragIndex = null
                        dragOffset = 0f
                        if (to != i) vm.moveStep(i, to)
                    },
                    onDragCancel = { dragIndex = null; dragOffset = 0f },
                )
            }
            StepCard(
                number = i + 1,
                row = row,
                playing = row.type == playingStep,
                editing = editing,
                canDrag = canDrag,
                canRemove = editing && rows.size > 1,
                onRemove = { vm.removeStep(row.type) },
                modifier = Modifier
                    .zIndex(if (dragging) 1f else 0f)
                    .graphicsLayer {
                        translationY = if (dragging) dragOffset else 0f
                        shadowElevation = if (dragging) 16f else 0f
                    }
                    .then(dragModifier),
            )
        }
    }

    val missing = RoutineSteps.missing(routine.steps)
    if (editing && missing.isNotEmpty()) {
        Spacer(Modifier.height(16.dp))
        var adding by remember { mutableStateOf(false) }
        if (!adding) {
            SecondaryAction("항목 추가", icon = TeumIcons.Add, modifier = Modifier.fillMaxWidth()) { adding = true }
        } else {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                missing.forEach { type ->
                    SecondaryAction(RoutineSteps.shortTitle(type), icon = TeumIcons.forStep(type), modifier = Modifier.weight(1f)) {
                        vm.addStep(type)
                        adding = false
                    }
                }
            }
        }
    }
}

private fun stepRow(type: String, state: UiState): StepRow {
    val plan = state.routine.plan
    val title = RoutineSteps.title(type)
    if (plan != null) {
        val skipped = plan.skipped.firstOrNull { it.type == type }
        if (skipped != null || type in state.routine.voiceFailed) {
            return StepRow(type, title, "정보 없음", null, missing = true)
        }
        val items = plan.items.filter { it.routineStep == type }
        if (items.isEmpty()) return StepRow(type, title, "정보 없음", null, missing = true)
        val total = items.sumOf { it.durationSec }
        val subtitle = if (type == RoutineSteps.RECOMMEND) {
            "이야기 ${items.count { !it.isAd }}편 · 남은 시간만큼"
        } else items.first().subtitle
        return StepRow(type, title, subtitle, Durations.approxMinutes(total), missing = false)
    }
    // 시작 전: 홈 퀵 메뉴 카드로 미리 보여 준다.
    if (type == RoutineSteps.RECOMMEND) {
        return StepRow(type, title, "남은 시간만큼 추천 이야기", state.destinationMin?.let { "약 ${it}분" }, missing = false)
    }
    val card = state.quickMenu.cards.firstOrNull { it.id == type }
    return when {
        card == null -> StepRow(type, title, "", null, missing = false)
        !card.available -> StepRow(type, title, unavailableHint(type), null, missing = true)
        else -> StepRow(type, title, card.subtitle, Durations.approxMinutes(card.durationSec), missing = false)
    }
}

@Composable
private fun StepCard(
    number: Int,
    row: StepRow,
    playing: Boolean,
    editing: Boolean,
    canDrag: Boolean,
    canRemove: Boolean,
    onRemove: () -> Unit,
    modifier: Modifier,
) {
    Row(
        modifier.fillMaxWidth().height(STEP_HEIGHT.dp).clip(RoundedCornerShape(20.dp))
            .background(if (playing) TeumColors.AccentSurface else Color.White)
            .border(2.dp, if (playing) TeumColors.Primary else TeumColors.CardBorder, RoundedCornerShape(20.dp))
            .padding(horizontal = 20.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            "$number", fontSize = 24.sp, fontWeight = FontWeight.Bold,
            color = if (row.missing) TeumColors.Disabled else TeumColors.Primary, modifier = Modifier.width(36.dp),
        )
        StepIcon(TeumIcons.forStep(row.type), size = 56, dimmed = row.missing)
        Spacer(Modifier.width(16.dp))
        Column(Modifier.weight(1f).alpha(if (row.missing) 0.55f else 1f)) {
            Text(row.title, fontSize = 23.sp, fontWeight = FontWeight.SemiBold, color = TeumColors.Text, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (row.subtitle.isNotEmpty()) {
                Text(row.subtitle, fontSize = 18.sp, color = TeumColors.SubText, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        row.duration?.let { Text(it, fontSize = 19.sp, color = TeumColors.SubText) }
        if (editing) {
            Spacer(Modifier.width(12.dp))
            if (canRemove) {
                Box(
                    Modifier.size(56.dp).clip(CircleShape).clickable(onClick = onRemove),
                    contentAlignment = Alignment.Center,
                ) { Icon(TeumIcons.Close, "${row.title} 빼기", tint = TeumColors.Icon, modifier = Modifier.size(28.dp)) }
            }
            Box(Modifier.size(56.dp).alpha(if (canDrag) 1f else 0.2f), contentAlignment = Alignment.Center) {
                Icon(TeumIcons.DragHandle, "끌어서 순서 바꾸기", tint = TeumColors.Icon, modifier = Modifier.size(32.dp))
            }
        }
    }
}

// ---- 오른쪽: 지금 듣는 중 ----

@Composable
private fun ColumnScope.PlayPanel(state: UiState, vm: MainViewModel) {
    val plan = state.routine.plan
    if (plan == null) {
        Column(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(28.dp)).background(TeumColors.AccentSurface).padding(32.dp),
        ) {
            Text("정한 순서대로 이어서 들려 드려요", fontSize = 24.sp, color = TeumColors.Text)
            Spacer(Modifier.height(24.dp))
            PrimaryAction("루틴 시작", TeumIcons.Play, Modifier.fillMaxWidth(), onClick = vm::startRoutine)
        }
        Spacer(Modifier.height(20.dp))
        ArrivalNote()
        return
    }
    val item = state.current
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(28.dp)).background(TeumColors.Panel)
            .border(1.dp, TeumColors.CardBorder, RoundedCornerShape(28.dp)).padding(28.dp),
    ) {
        if (state.ended || item == null) {
            Text("루틴을 마쳤어요", fontSize = 30.sp, fontWeight = FontWeight.Bold, color = TeumColors.Text)
            Spacer(Modifier.height(8.dp))
            Text(if (state.arrived) "목적지에 도착해 루틴을 끝냈어요" else "정한 순서를 모두 들었어요", fontSize = 20.sp, color = TeumColors.SubText)
            Spacer(Modifier.height(24.dp))
            SecondaryAction("다시 시작", icon = TeumIcons.Play, modifier = Modifier.fillMaxWidth(), onClick = vm::startRoutine)
            return@Column
        }
        val stepTitle = item.routineStep?.let { RoutineSteps.title(it) } ?: ""
        Text("지금 듣는 중 · $stepTitle", fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = TeumColors.Primary)
        Spacer(Modifier.height(10.dp))
        Text(item.title, fontSize = 30.sp, fontWeight = FontWeight.Bold, color = TeumColors.Text, maxLines = 2, overflow = TextOverflow.Ellipsis)
        Spacer(Modifier.height(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Tag(
                when {
                    item.isBriefing -> "AI 음성"
                    item.isAd -> "광고"
                    else -> Topics.labelForCategory(item.category) ?: "이야기"
                },
            )
            if (item.isBriefing && item.subtitle.isNotEmpty()) {
                Text(item.subtitle, fontSize = 19.sp, color = TeumColors.SubText, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        if (item.sources.isNotEmpty()) {
            Spacer(Modifier.height(8.dp))
            Text("출처 " + item.sources.joinToString(" · "), fontSize = 15.sp, color = TeumColors.SubText)
        }
        Spacer(Modifier.height(20.dp))
        val progress = if (state.durationMs > 0) (state.positionMs.toFloat() / state.durationMs).coerceIn(0f, 1f) else 0f
        LinearProgressIndicator(
            progress = { progress },
            modifier = Modifier.fillMaxWidth().height(8.dp).clip(RoundedCornerShape(4.dp)),
            color = TeumColors.Primary, trackColor = TeumColors.Track, strokeCap = StrokeCap.Round,
            gapSize = 0.dp, drawStopIndicator = {},
        )
        Row(Modifier.fillMaxWidth().padding(top = 8.dp)) {
            Text(formatTime(state.positionMs), fontSize = 18.sp, color = TeumColors.SubText)
            Spacer(Modifier.weight(1f))
            Text(formatTime(state.durationMs), fontSize = 18.sp, color = TeumColors.SubText)
        }
        state.playerError?.let {
            Spacer(Modifier.height(8.dp))
            Text(it, fontSize = 20.sp, color = TeumColors.Text)
        }
        Spacer(Modifier.height(16.dp))
        val isAd = item.isAd
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly, verticalAlignment = Alignment.CenterVertically) {
            TransportButton(TeumIcons.Previous, "이전", enabled = !isAd && state.hasPrevious, onClick = vm::previous)
            Box(
                Modifier.size(width = 136.dp, height = 112.dp).clip(RoundedCornerShape(32.dp))
                    .background(TeumColors.Primary).clickable(onClick = vm::togglePlay),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    if (state.isPlaying) TeumIcons.Pause else TeumIcons.Play,
                    if (state.isPlaying) "일시정지" else "재생", tint = Color.White, modifier = Modifier.size(56.dp),
                )
            }
            TransportButton(TeumIcons.Next, "다음", enabled = (!isAd || state.playerError != null) && state.hasNext, onClick = vm::next)
        }
    }

    Spacer(Modifier.height(20.dp))
    val next = if (state.ended) emptyList() else state.upNext.take(3)
    if (next.isNotEmpty()) {
        SectionTitle("다음 순서")
        Spacer(Modifier.height(12.dp))
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) { next.forEach { NextRow(it) } }
        Spacer(Modifier.height(20.dp))
    }
    ArrivalNote()
}

@Composable
private fun NextRow(item: PlaylistItem) {
    Row(
        Modifier.fillMaxWidth().height(72.dp).clip(RoundedCornerShape(16.dp)).background(Color.White)
            .border(1.dp, TeumColors.CardBorder, RoundedCornerShape(16.dp)).padding(horizontal = 20.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            item.routineStep?.let { RoutineSteps.shortTitle(it) } ?: "", fontSize = 18.sp, fontWeight = FontWeight.SemiBold,
            color = TeumColors.Primary, modifier = Modifier.width(56.dp),
        )
        Text(item.title, fontSize = 21.sp, color = TeumColors.Text, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        Text(formatTime((item.durationSec * 1000).toLong()), fontSize = 18.sp, color = TeumColors.SubText)
    }
}

@Composable
private fun ArrivalNote() {
    Text(
        "도착이 가까워지면 듣던 편을 마무리하고 루틴을 끝내요", fontSize = 18.sp, color = TeumColors.SubText,
        textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth(),
    )
}

@Composable
fun Tag(text: String) {
    Text(
        text, fontSize = 18.sp, fontWeight = FontWeight.SemiBold, color = TeumColors.Primary,
        modifier = Modifier.clip(RoundedCornerShape(10.dp)).background(TeumColors.AccentSurface)
            .padding(horizontal = 12.dp, vertical = 4.dp),
    )
}

private const val STEP_HEIGHT = 104
private const val STEP_GAP = 12
