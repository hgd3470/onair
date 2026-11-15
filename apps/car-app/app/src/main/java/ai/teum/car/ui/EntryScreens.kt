package ai.teum.car.ui

import ai.teum.car.data.Durations
import ai.teum.car.data.Topics
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// ---------------- S01 홈 ----------------

@Composable
fun HomeScreen(state: UiState, vm: MainViewModel) {
    Column(Modifier.fillMaxSize().background(TeumColors.Background)) {
        Row(
            Modifier.fillMaxWidth().height(72.dp).padding(horizontal = 32.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("온에어", fontSize = 34.sp, fontWeight = FontWeight.Bold, color = TeumColors.EntryPurple)
            if (state.family) {
                Spacer(Modifier.width(16.dp))
                Text(
                    "가족 콘텐츠", fontSize = 18.sp, fontWeight = FontWeight.SemiBold, color = TeumColors.Primary,
                    modifier = Modifier.clip(RoundedCornerShape(20.dp)).background(TeumColors.AccentSurface)
                        .padding(horizontal = 14.dp, vertical = 6.dp),
                )
            }
            Spacer(Modifier.weight(1f))
            if (state.hasSession) {
                NowPlayingChip(state.current?.title ?: "", onClick = vm::openPlayer)
            }
        }
        BoxWithConstraints(Modifier.fillMaxSize()) {
            val wide = maxWidth > maxHeight
            val blocks: @Composable (Modifier) -> Unit = { m ->
                EntryBlock(
                    title = "추천 듣기",
                    subtitle = state.destinationMin?.let { "목적지까지 ${it}분 동안 들어요" } ?: "고르지 않아도 바로 들어요",
                    background = TeumColors.EntryPurple,
                    content = Color.White,
                    modifier = m,
                    onClick = vm::startRecommend,
                )
                // 오른쪽 칸은 위 '골라 듣기', 아래 '깊이 듣기'(두 사람 팟캐스트)로 나눈다. 회차가 없으면 골라 듣기만 둔다.
                Column(m) {
                    val deep = state.longform.isNotEmpty()
                    EntryBlock(
                        title = "골라 듣기",
                        subtitle = "주제와 시간을 정해서 들어요",
                        background = TeumColors.SecondaryButton,
                        content = TeumColors.Text,
                        modifier = Modifier.weight(1f).fillMaxWidth(),
                        compact = deep,
                        onClick = vm::openPick,
                    )
                    if (deep) {
                        DeepEntry(state, Modifier.weight(1f).fillMaxWidth(), onClick = vm::openDeep)
                    }
                }
            }
            if (wide) {
                Column(Modifier.fillMaxSize()) {
                    Row(Modifier.weight(1f).fillMaxWidth()) { blocks(Modifier.weight(1f).fillMaxSize()) }
                    Row(
                        Modifier.weight(1f).fillMaxWidth().padding(24.dp),
                        horizontalArrangement = Arrangement.spacedBy(24.dp),
                    ) {
                        QuickMenuSection(state, vm, Modifier.weight(2f).fillMaxHeight())
                        RoutineCard(state, vm, Modifier.weight(1f).fillMaxHeight())
                    }
                }
            } else {
                Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
                    blocks(Modifier.fillMaxWidth().height(220.dp))
                    Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(24.dp)) {
                        QuickMenuSection(state, vm, Modifier.fillMaxWidth())
                        RoutineCard(state, vm, Modifier.fillMaxWidth())
                    }
                }
            }
        }
    }
}

@Composable
private fun NowPlayingChip(title: String, onClick: () -> Unit) {
    Row(
        Modifier.clip(RoundedCornerShape(40.dp)).background(TeumColors.AccentSurface)
            .clickable(onClick = onClick).padding(horizontal = 20.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("듣는 중 · ", color = TeumColors.Primary, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
        Text(title, color = TeumColors.Text, fontSize = 18.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.widthIn(max = 280.dp))
        Icon(TeumIcons.Arrow, null, tint = TeumColors.Primary, modifier = Modifier.padding(start = 8.dp).size(22.dp))
    }
}

@Composable
private fun EntryBlock(
    title: String,
    subtitle: String,
    background: Color,
    content: Color,
    modifier: Modifier,
    compact: Boolean = false,
    onClick: () -> Unit,
) {
    val interaction = remember { MutableInteractionSource() }
    val pressed by interaction.collectIsPressedAsState()
    Box(
        modifier
            .background(background)
            .clickable(interactionSource = interaction, indication = null, onClick = onClick)
            .background(if (pressed) Color.Black.copy(alpha = 0.14f) else Color.Transparent)
            .padding(horizontal = 48.dp, vertical = if (compact) 20.dp else 40.dp),
    ) {
        Column(Modifier.align(Alignment.CenterStart)) {
            if (!compact) {
                WaveBars(color = content.copy(alpha = 0.85f))
                Spacer(Modifier.height(28.dp))
            }
            Text(title, fontSize = if (compact) 34.sp else 40.sp, fontWeight = FontWeight.Bold, color = content)
            Spacer(Modifier.height(if (compact) 6.dp else 10.dp))
            Text(subtitle, fontSize = if (compact) 20.sp else 22.sp, color = content.copy(alpha = 0.75f))
        }
        Icon(
            TeumIcons.Arrow, null, tint = content,
            modifier = Modifier.align(if (compact) Alignment.CenterEnd else Alignment.BottomEnd).size(if (compact) 44.dp else 56.dp),
        )
    }
}

/** 홈 오른쪽 아래 칸: 깊이 듣기(두 사람이 나누는 8~9분 팟캐스트) 입구. */
@Composable
private fun DeepEntry(state: UiState, modifier: Modifier, onClick: () -> Unit) {
    val interaction = remember { MutableInteractionSource() }
    val pressed by interaction.collectIsPressedAsState()
    Box(
        modifier
            .background(TeumColors.AccentSurface)
            .clickable(interactionSource = interaction, indication = null, onClick = onClick)
            .background(if (pressed) Color.Black.copy(alpha = 0.08f) else Color.Transparent)
            .padding(horizontal = 48.dp, vertical = 20.dp),
    ) {
        Row(Modifier.align(Alignment.CenterStart), verticalAlignment = Alignment.CenterVertically) {
            HostAvatars("", size = 56, showNames = false)
            Spacer(Modifier.width(24.dp))
            Column {
                Text("깊이 듣기", fontSize = 34.sp, fontWeight = FontWeight.Bold, color = TeumColors.EntryPurple)
                Spacer(Modifier.height(6.dp))
                Text(
                    "두 사람이 나누는 팟캐스트 · ${state.longform.size}편",
                    fontSize = 20.sp, color = TeumColors.Primary,
                )
            }
        }
        Icon(TeumIcons.Arrow, null, tint = TeumColors.EntryPurple, modifier = Modifier.align(Alignment.CenterEnd).size(44.dp))
    }
}

@Composable
fun WaveBars(color: Color, heights: List<Int> = listOf(14, 30, 46, 26, 38, 18, 10)) {
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
        heights.forEach { h ->
            Box(Modifier.width(7.dp).height(h.dp).clip(RoundedCornerShape(4.dp)).background(color))
        }
    }
}

// ---------------- S02 골라 듣기 ----------------

@Composable
fun PickScreen(state: UiState, vm: MainViewModel) {
    Column(Modifier.fillMaxSize().background(TeumColors.Background)) {
        Row(Modifier.fillMaxWidth().padding(start = 24.dp, top = 16.dp, end = 24.dp), verticalAlignment = Alignment.CenterVertically) {
            CircleIconButton(TeumIcons.Back, "이전", onClick = vm::goHome)
            Spacer(Modifier.width(20.dp))
            Text("골라 듣기", fontSize = 30.sp, fontWeight = FontWeight.Bold, color = TeumColors.Text)
        }
        Column(
            Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState())
                .padding(horizontal = 40.dp, vertical = 16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Column(Modifier.widthIn(max = 820.dp).fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("듣고 싶은 주제를 모두 고르세요", fontSize = 22.sp, color = TeumColors.SubText)
                Topics.groups.forEach { g ->
                    TopicTile(
                        label = "${g.id}  ${g.label}",
                        keywords = g.keywords,
                        selected = g.id in state.selectedGroups,
                        onClick = { vm.toggleGroup(g.id) },
                    )
                }
                Spacer(Modifier.height(12.dp))
                Text("얼마나 들을까요?", fontSize = 22.sp, color = TeumColors.SubText)
                Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    Durations.manualMinutes.forEach { m ->
                        TimeChip(Durations.label(m), selected = state.minutes == m, modifier = Modifier.weight(1f)) {
                            vm.selectMinutes(m)
                        }
                    }
                    // 경로 안내 중일 때만: 내비 남은 시간(remain_sec)으로 편성
                    state.destinationMin?.let { min ->
                        TimeChip("도착까지 ${min}분", selected = state.minutes == Durations.UNTIL_ARRIVAL, modifier = Modifier.weight(1.3f)) {
                            vm.selectMinutes(Durations.UNTIL_ARRIVAL)
                        }
                    }
                }
            }
        }
        val count = state.selectedGroups.size
        Box(Modifier.fillMaxWidth().padding(horizontal = 40.dp, vertical = 24.dp), contentAlignment = Alignment.Center) {
            Button(
                onClick = vm::startPick,
                enabled = count > 0,
                modifier = Modifier.widthIn(max = 820.dp).fillMaxWidth().height(84.dp),
                shape = RoundedCornerShape(20.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = TeumColors.EntryPurple,
                    disabledContainerColor = TeumColors.SecondaryButton,
                    disabledContentColor = TeumColors.SubText,
                ),
            ) {
                Text(
                    if (count > 0) "선택한 ${count}개 주제 · ${pickTimeLabel(state)} 듣기" else "주제를 선택해주세요",
                    fontSize = 26.sp, fontWeight = FontWeight.SemiBold,
                )
            }
        }
    }
}

private fun pickTimeLabel(state: UiState): String =
    if (state.minutes == Durations.UNTIL_ARRIVAL) {
        state.destinationMin?.let { "도착까지 ${it}분" } ?: Durations.label(Durations.DEFAULT_MINUTES)
    } else Durations.label(state.minutes)

@Composable
private fun TopicTile(label: String, keywords: String, selected: Boolean, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 84.dp).clip(RoundedCornerShape(18.dp))
            .background(if (selected) TeumColors.AccentSurface else TeumColors.Panel)
            .border(BorderStroke(2.dp, if (selected) TeumColors.EntryPurple else TeumColors.CardBorder), RoundedCornerShape(18.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 24.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(label, fontSize = 26.sp, fontWeight = FontWeight.SemiBold, color = TeumColors.Text)
            Text(keywords, fontSize = 18.sp, color = TeumColors.SubText)
        }
        Box(
            Modifier.size(40.dp).clip(CircleShape)
                .background(if (selected) TeumColors.EntryPurple else Color.Transparent)
                .border(2.dp, if (selected) TeumColors.EntryPurple else TeumColors.Disabled, CircleShape),
            contentAlignment = Alignment.Center,
        ) {
            if (selected) Icon(TeumIcons.Check, "선택됨", tint = Color.White, modifier = Modifier.size(26.dp))
        }
    }
}

@Composable
private fun TimeChip(label: String, selected: Boolean, modifier: Modifier, onClick: () -> Unit) {
    Box(
        modifier.height(72.dp).clip(RoundedCornerShape(18.dp))
            .background(if (selected) TeumColors.EntryPurple else TeumColors.SecondaryButton)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, fontSize = 24.sp, fontWeight = FontWeight.SemiBold, color = if (selected) Color.White else TeumColors.Text)
    }
}

@Composable
fun CircleIconButton(icon: ImageVector, description: String, onClick: () -> Unit) {
    Box(
        Modifier.size(64.dp).clip(CircleShape).background(TeumColors.SecondaryButton).clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Icon(icon, description, tint = TeumColors.Icon, modifier = Modifier.size(32.dp))
    }
}

// ---------------- 목록 준비 · 오류 ----------------

@Composable
fun LoadingScreen(state: UiState, vm: MainViewModel) {
    CenterMessage(
        icon = { CircularProgressIndicator(color = TeumColors.Primary, strokeWidth = 6.dp, modifier = Modifier.size(80.dp)) },
        title = state.loadingText,
        body = null,
    ) {
        OutlinedButton(onClick = vm::goHome, modifier = Modifier.height(72.dp).widthIn(min = 200.dp)) {
            Text("취소", fontSize = 24.sp, color = TeumColors.Text)
        }
    }
}

@Composable
fun LoadErrorScreen(state: UiState, vm: MainViewModel) {
    val empty = state.loadProblem == LoadProblem.Empty
    val voice = state.loadProblem == LoadProblem.Voice
    CenterMessage(
        icon = { Icon(TeumIcons.Clock, null, tint = TeumColors.Icon, modifier = Modifier.size(80.dp)) },
        title = when {
            empty -> "지금 들려드릴 새 이야기가 없어요"
            voice -> "브리핑 음성을 만들지 못했어요"
            else -> "서버에 연결하지 못했어요"
        },
        body = when {
            empty -> "이미 다 들은 이야기는 목록에서 빠져요."
            voice -> "기기의 음성 읽기(TTS) 설정을 확인해주세요."
            else -> "네트워크 연결을 확인한 뒤 다시 시도해주세요."
        },
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            OutlinedButton(onClick = vm::goHome, modifier = Modifier.height(72.dp).widthIn(min = 180.dp)) {
                Text("홈", fontSize = 24.sp, color = TeumColors.Text)
            }
            Button(
                onClick = if (empty) vm::resetUserAndRetry else vm::retryLoad,
                modifier = Modifier.height(72.dp).widthIn(min = 220.dp),
                colors = ButtonDefaults.buttonColors(containerColor = TeumColors.Primary),
            ) {
                Text(if (empty) "새 사용자로 다시 받기" else "다시 시도", fontSize = 24.sp)
            }
        }
    }
}

@Composable
private fun CenterMessage(
    icon: @Composable () -> Unit,
    title: String,
    body: String?,
    actions: @Composable () -> Unit,
) {
    Column(
        Modifier.fillMaxSize().background(TeumColors.Background).padding(40.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        icon()
        Spacer(Modifier.height(28.dp))
        Text(title, fontSize = 30.sp, fontWeight = FontWeight.Bold, color = TeumColors.Text, textAlign = TextAlign.Center)
        if (body != null) {
            Spacer(Modifier.height(12.dp))
            Text(body, fontSize = 22.sp, color = TeumColors.SubText, textAlign = TextAlign.Center)
        }
        Spacer(Modifier.height(36.dp))
        actions()
    }
}
