package ai.teum.car.ui

import ai.teum.car.data.BriefingCard
import ai.teum.car.data.Durations
import ai.teum.car.data.RoutineSteps
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// ---------------- S01 홈: 퀵 메뉴 ----------------

@Composable
fun QuickMenuSection(state: UiState, vm: MainViewModel, modifier: Modifier) {
    val menu = state.quickMenu
    Column(modifier, verticalArrangement = Arrangement.spacedBy(14.dp)) {
        SectionTitle("퀵 메뉴")
        when {
            menu.cards.isNotEmpty() -> Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                menu.cards.forEach { card ->
                    QuickCard(card, Modifier.weight(1f).fillMaxHeight()) { vm.playBriefing(card) }
                }
            }
            menu.loading -> PanelMessage { CircularProgressIndicator(color = TeumColors.Primary, modifier = Modifier.size(48.dp)) }
            else -> PanelMessage {
                Text("브리핑 정보를 불러오지 못했어요", fontSize = 22.sp, color = TeumColors.SubText)
                Spacer(Modifier.height(12.dp))
                SecondaryAction("다시 불러오기") { vm.refreshQuickMenu(force = true) }
            }
        }
    }
}

@Composable
private fun QuickCard(card: BriefingCard, modifier: Modifier, onPlay: () -> Unit) {
    val enabled = card.available
    Column(
        modifier.heightIn(min = 220.dp).clip(RoundedCornerShape(24.dp)).background(TeumColors.Panel)
            .border(1.dp, TeumColors.CardBorder, RoundedCornerShape(24.dp))
            .clickable(enabled = enabled, onClick = onPlay)
            .alpha(if (enabled) 1f else 0.45f)
            .padding(24.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            StepIcon(TeumIcons.forStep(card.id))
            Spacer(Modifier.weight(1f))
            Box(
                Modifier.size(72.dp).clip(CircleShape).background(if (enabled) TeumColors.Primary else TeumColors.Disabled),
                contentAlignment = Alignment.Center,
            ) {
                Icon(TeumIcons.Play, "${card.title} 재생", tint = Color.White, modifier = Modifier.size(40.dp))
            }
        }
        Spacer(Modifier.height(16.dp))
        Text(card.title, fontSize = 26.sp, fontWeight = FontWeight.Bold, color = TeumColors.Text, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Spacer(Modifier.height(6.dp))
        Text(
            if (enabled) card.subtitle else unavailableHint(card.id),
            fontSize = 20.sp, color = TeumColors.SubText, maxLines = 2, overflow = TextOverflow.Ellipsis,
        )
        Spacer(Modifier.height(12.dp))
        if (enabled) Text(Durations.approxMinutes(card.durationSec), fontSize = 18.sp, color = TeumColors.Primary, fontWeight = FontWeight.SemiBold)
        if (card.sources.isNotEmpty()) {
            Text(
                "출처 " + card.sources.joinToString(" · "), fontSize = 15.sp, color = TeumColors.SubText,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

/** available=false 일 때 subtitle 대신 보일 짧은 안내. */
fun unavailableHint(id: String) = when (id) {
    RoutineSteps.WEATHER -> "위치 정보가 없어요"
    RoutineSteps.TRAFFIC -> "경로 안내 중이 아니에요"
    else -> "지금은 정보가 없어요"
}

// ---------------- S01 홈: 내 루틴 ----------------

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun RoutineCard(state: UiState, vm: MainViewModel, modifier: Modifier) {
    val routine = state.activeRoutine
    Column(modifier, verticalArrangement = Arrangement.spacedBy(14.dp)) {
        SectionTitle("내 루틴")
        Column(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(24.dp)).background(TeumColors.AccentSurface)
                .padding(24.dp),
        ) {
            if (routine == null) {
                Text("루틴을 불러오고 있어요", fontSize = 22.sp, color = TeumColors.SubText)
                return@Column
            }
            Text(routine.name, fontSize = 30.sp, fontWeight = FontWeight.Bold, color = TeumColors.Text, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.height(6.dp))
            Text(routineSubtitle(routine.label, state.destinationMin), fontSize = 20.sp, color = TeumColors.SubText, maxLines = 1)
            Spacer(Modifier.height(16.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                routine.steps.forEachIndexed { i, step ->
                    if (i > 0) Text("→", fontSize = 20.sp, color = TeumColors.SubText, modifier = Modifier.align(Alignment.CenterVertically))
                    Text(
                        RoutineSteps.shortTitle(step), fontSize = 19.sp, fontWeight = FontWeight.SemiBold, color = TeumColors.Primary,
                        modifier = Modifier.clip(RoundedCornerShape(16.dp)).background(Color.White)
                            .padding(horizontal = 14.dp, vertical = 8.dp),
                    )
                }
            }
            Spacer(Modifier.height(24.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                SecondaryAction(
                    "순서 편집",
                    icon = if (state.editLocked) TeumIcons.Lock else TeumIcons.Edit,
                    dimmed = state.editLocked,
                    modifier = Modifier.weight(1f),
                    onClick = vm::openRoutineEditor,
                )
                PrimaryAction("루틴 시작", TeumIcons.Play, Modifier.weight(1.2f), onClick = vm::startRoutine)
            }
        }
    }
}

fun routineSubtitle(label: String?, destinationMin: Int?): String =
    listOfNotNull(label, destinationMin?.let { "목적지까지 ${it}분" }).joinToString(" · ").ifEmpty { "루틴" }

// ---------------- 가족 콘텐츠 확인 ----------------

/** 뒷좌석 탑승 시 주행당 한 번. 큰 버튼 두 개, 키보드 없음. */
@Composable
fun FamilyPrompt(vm: MainViewModel) {
    Box(
        Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.45f)).clickable(enabled = true, onClick = {}),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            Modifier.widthIn(max = 760.dp).fillMaxWidth().padding(32.dp).clip(RoundedCornerShape(32.dp))
                .background(Color.White).padding(40.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                "뒷좌석에 동승자가 있어요.\n가족 콘텐츠로 바꿀까요?", fontSize = 32.sp, fontWeight = FontWeight.Bold,
                color = TeumColors.Text, textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(36.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                SecondaryAction("아니요", modifier = Modifier.weight(1f)) { vm.answerFamily(false) }
                PrimaryAction("예", null, Modifier.weight(1f)) { vm.answerFamily(true) }
            }
        }
    }
}

// ---------------- 공통 ----------------

@Composable
fun SectionTitle(text: String) {
    Text(text, fontSize = 26.sp, fontWeight = FontWeight.Bold, color = TeumColors.Text)
}

@Composable
fun StepIcon(icon: ImageVector, size: Int = 64, dimmed: Boolean = false) {
    Box(
        Modifier.size(size.dp).clip(RoundedCornerShape(18.dp)).background(if (dimmed) TeumColors.SecondaryButton else TeumColors.AccentSurface),
        contentAlignment = Alignment.Center,
    ) {
        Icon(icon, null, tint = if (dimmed) TeumColors.Disabled else TeumColors.Primary, modifier = Modifier.size((size * 0.55f).dp))
    }
}

@Composable
private fun PanelMessage(content: @Composable () -> Unit) {
    Column(
        Modifier.fillMaxWidth().heightIn(min = 200.dp).clip(RoundedCornerShape(24.dp)).background(TeumColors.Panel).padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) { content() }
}

@Composable
fun PrimaryAction(label: String, icon: ImageVector?, modifier: Modifier = Modifier, enabled: Boolean = true, onClick: () -> Unit) {
    Row(
        modifier.height(76.dp).clip(RoundedCornerShape(20.dp))
            .background(if (enabled) TeumColors.EntryPurple else TeumColors.Disabled)
            .clickable(enabled = enabled, onClick = onClick).padding(horizontal = 20.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (icon != null) {
            Icon(icon, null, tint = Color.White, modifier = Modifier.size(32.dp))
            Spacer(Modifier.width(8.dp))
        }
        Text(label, fontSize = 24.sp, fontWeight = FontWeight.SemiBold, color = Color.White, maxLines = 1)
    }
}

@Composable
fun SecondaryAction(
    label: String,
    icon: ImageVector? = null,
    dimmed: Boolean = false,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    Row(
        modifier.height(76.dp).clip(RoundedCornerShape(20.dp)).background(Color.White)
            .border(2.dp, TeumColors.CardBorder, RoundedCornerShape(20.dp))
            .clickable(onClick = onClick).alpha(if (dimmed) 0.5f else 1f).padding(horizontal = 20.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (icon != null) {
            Icon(icon, null, tint = TeumColors.Icon, modifier = Modifier.size(28.dp))
            Spacer(Modifier.width(8.dp))
        }
        Text(label, fontSize = 24.sp, fontWeight = FontWeight.SemiBold, color = TeumColors.Text, maxLines = 1)
    }
}
