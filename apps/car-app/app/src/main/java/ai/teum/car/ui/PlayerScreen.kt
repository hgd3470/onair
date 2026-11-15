package ai.teum.car.ui

import ai.teum.car.data.PlaylistItem
import ai.teum.car.data.Speeds
import ai.teum.car.data.Topics
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// ---------------- S04 스트리밍 / S05 광고 ----------------

@Composable
fun PlayerScreen(state: UiState, vm: MainViewModel) {
    Column(Modifier.fillMaxSize().background(TeumColors.Background)) {
        Row(
            Modifier.fillMaxWidth().padding(start = 24.dp, top = 16.dp, end = 24.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            CircleIconButton(TeumIcons.Home, "홈", onClick = vm::goHome)
            Spacer(Modifier.width(20.dp))
            Text(state.mode.label, fontSize = 24.sp, fontWeight = FontWeight.SemiBold, color = TeumColors.Primary)
        }
        BoxWithConstraints(Modifier.fillMaxSize()) {
            if (maxWidth > maxHeight) {
                Row(Modifier.fillMaxSize().padding(24.dp), horizontalArrangement = Arrangement.spacedBy(28.dp)) {
                    Column(Modifier.weight(1.15f).fillMaxHeight(), verticalArrangement = Arrangement.Center) {
                        NowPlaying(state, vm)
                    }
                    Column(Modifier.weight(1f).fillMaxHeight()) { UpNext(state) }
                }
            } else {
                Column(
                    Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 40.dp, vertical = 16.dp),
                ) {
                    NowPlaying(state, vm)
                    Spacer(Modifier.height(28.dp))
                    UpNext(state)
                }
            }
        }
    }
}

@Composable
private fun ColumnScope.NowPlaying(state: UiState, vm: MainViewModel) {
    val item = state.current
    val isAd = item?.isAd == true
    val isBriefing = item?.isBriefing == true

    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        if (isAd) {
            Text(
                "광고", fontSize = 22.sp, fontWeight = FontWeight.Bold, color = Color.White,
                modifier = Modifier.clip(RoundedCornerShape(10.dp)).background(TeumColors.Icon)
                    .padding(horizontal = 18.dp, vertical = 6.dp),
            )
        } else if (isBriefing) {
            Tag("브리핑 · AI 음성")
        } else if (item?.isLongform == true) {
            Tag("깊이 듣기 · 두 사람 팟캐스트")
        } else {
            Text(item?.let { Topics.labelForCategory(it.category) } ?: "", fontSize = 22.sp, color = TeumColors.SubText)
        }
        Spacer(Modifier.height(16.dp))
        if (item?.isLongform == true) {
            Box(Modifier.height(150.dp), contentAlignment = Alignment.Center) { HostAvatars(item.subtitle, size = 110) }
        } else {
            Box(
                Modifier.size(150.dp).clip(CircleShape).background(TeumColors.AccentSurface),
                contentAlignment = Alignment.Center,
            ) {
                WaveBars(color = if (state.isPlaying) TeumColors.Primary else TeumColors.Disabled)
            }
        }
        Spacer(Modifier.height(20.dp))
        Text(
            item?.title ?: "", fontSize = 32.sp, fontWeight = FontWeight.Bold, color = TeumColors.Text,
            textAlign = TextAlign.Center, maxLines = 2, overflow = TextOverflow.Ellipsis,
        )
        if (isAd) {
            Spacer(Modifier.height(8.dp))
            Text("광고가 끝나면 다음 이야기가 자동으로 재생돼요", fontSize = 20.sp, color = TeumColors.SubText, textAlign = TextAlign.Center)
        }
        if (item?.isLongform == true && item.subtitle.isNotEmpty()) {
            Spacer(Modifier.height(8.dp))
            Text(item.subtitle, fontSize = 22.sp, fontWeight = FontWeight.SemiBold, color = TeumColors.EntryPurple, textAlign = TextAlign.Center, maxLines = 1)
        }
        if (isBriefing && item != null) {
            // 화면에는 숫자 표기 부제(subtitle)를 보인다. 낭독 문장(text)은 소리로만.
            Spacer(Modifier.height(8.dp))
            Text(item.subtitle, fontSize = 22.sp, color = TeumColors.Text, textAlign = TextAlign.Center, maxLines = 2)
            if (item.sources.isNotEmpty()) {
                Spacer(Modifier.height(6.dp))
                Text("출처 " + item.sources.joinToString(" · "), fontSize = 15.sp, color = TeumColors.SubText, textAlign = TextAlign.Center)
            }
        }
    }

    Spacer(Modifier.height(24.dp))
    val progress = if (state.durationMs > 0) (state.positionMs.toFloat() / state.durationMs).coerceIn(0f, 1f) else 0f
    LinearProgressIndicator(
        progress = { progress },
        modifier = Modifier.fillMaxWidth().height(8.dp).clip(RoundedCornerShape(4.dp)),
        color = TeumColors.Primary,
        trackColor = TeumColors.Track,
        strokeCap = StrokeCap.Round,
        gapSize = 0.dp,
        drawStopIndicator = {},
    )
    Row(Modifier.fillMaxWidth().padding(top = 8.dp)) {
        Text(formatTime(state.positionMs), fontSize = 18.sp, color = TeumColors.SubText)
        Spacer(Modifier.weight(1f))
        Text(formatTime(state.durationMs), fontSize = 18.sp, color = TeumColors.SubText)
    }

    state.playerError?.let { err ->
        Spacer(Modifier.height(12.dp))
        Row(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(TeumColors.Panel).padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(err, fontSize = 20.sp, color = TeumColors.Text, modifier = Modifier.weight(1f))
            TextAction("다시 시도", onClick = vm::retryPlayback)
            if (state.hasNext) TextAction("다음 이야기", onClick = vm::next)
        }
    }

    Spacer(Modifier.height(20.dp))
    val skipEnabled = !isAd || state.playerError != null
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceEvenly,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        TransportButton(TeumIcons.Previous, "이전", enabled = !isAd && state.hasPrevious, onClick = vm::previous)
        Box(
            Modifier.size(width = 136.dp, height = 112.dp).clip(RoundedCornerShape(32.dp))
                .background(TeumColors.Primary).clickable(onClick = vm::togglePlay),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                if (state.isPlaying) TeumIcons.Pause else TeumIcons.Play,
                if (state.isPlaying) "일시정지" else "재생",
                tint = Color.White, modifier = Modifier.size(56.dp),
            )
        }
        TransportButton(TeumIcons.Next, "다음", enabled = skipEnabled && state.hasNext, onClick = vm::next)
    }

    Spacer(Modifier.height(20.dp))
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceEvenly,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SpeedButton(state.speed, vm::setSpeed)
        if (!isAd && !isBriefing && item != null && !item.isLongform) {
            val reaction = state.reactions[item.contentId]
            ToggleButton(TeumIcons.Heart, "좋아요", selected = reaction?.liked == true, onClick = vm::toggleLike)
            ToggleButton(TeumIcons.Bookmark, "나중에 듣기", selected = reaction?.saved == true, onClick = vm::toggleSave)
        }
    }
}

@Composable
private fun UpNext(state: UiState) {
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(24.dp)).background(TeumColors.Panel).padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(if (state.mode == ListenMode.Briefing) "다음 순서" else "다음 이야기", fontSize = 24.sp, fontWeight = FontWeight.Bold, color = TeumColors.Text)
        val next = state.upNext
        if (next.isEmpty()) {
            Text(
                when {
                    state.arrived -> "목적지에 도착해 듣던 편까지만 들려 드렸어요"
                    state.mode == ListenMode.Briefing -> if (state.ended) "브리핑을 다 들었어요" else "브리핑이 끝나면 멈춰요"
                    state.ended -> "모든 이야기를 다 들었어요"
                    else -> "이 이야기가 마지막이에요"
                },
                fontSize = 20.sp, color = TeumColors.SubText,
            )
        }
        next.forEachIndexed { i, item -> UpNextCard(i + 1, item) }
    }
}

@Composable
private fun UpNextCard(number: Int, item: PlaylistItem) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 72.dp).clip(RoundedCornerShape(16.dp)).background(Color.White)
            .border(1.dp, TeumColors.CardBorder, RoundedCornerShape(16.dp)).padding(horizontal = 20.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("$number", fontSize = 22.sp, fontWeight = FontWeight.Bold, color = TeumColors.Primary, modifier = Modifier.width(36.dp))
        Column(Modifier.weight(1f)) {
            Text(item.title, fontSize = 21.sp, color = TeumColors.Text, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(
                when {
                    item.isAd -> "광고"
                    item.isBriefing -> "브리핑 · AI 음성"
                    item.isLongform -> item.subtitle
                    else -> Topics.labelForCategory(item.category) ?: ""
                },
                fontSize = 17.sp, color = TeumColors.SubText,
            )
        }
        Text(formatTime((item.durationSec * 1000).toLong()), fontSize = 18.sp, color = TeumColors.SubText)
    }
}

@Composable
fun TransportButton(icon: ImageVector, label: String, enabled: Boolean, onClick: () -> Unit) {
    Column(
        Modifier.size(width = 112.dp, height = 96.dp).clip(RoundedCornerShape(20.dp))
            .clickable(enabled = enabled, onClick = onClick).alpha(if (enabled) 1f else 0.35f),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(icon, label, tint = TeumColors.Icon, modifier = Modifier.size(40.dp))
        Text(label, fontSize = 20.sp, color = TeumColors.Icon)
    }
}

@Composable
private fun ToggleButton(icon: ImageVector, label: String, selected: Boolean, onClick: () -> Unit) {
    Column(
        Modifier.widthIn(min = 112.dp).clip(RoundedCornerShape(20.dp)).clickable(onClick = onClick).padding(8.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(
            Modifier.size(72.dp).clip(RoundedCornerShape(20.dp))
                .background(if (selected) TeumColors.Primary else TeumColors.SecondaryButton),
            contentAlignment = Alignment.Center,
        ) {
            Icon(icon, null, tint = if (selected) Color.White else TeumColors.Icon, modifier = Modifier.size(34.dp))
        }
        Spacer(Modifier.height(6.dp))
        Text(label, fontSize = 18.sp, color = if (selected) TeumColors.Primary else TeumColors.Icon)
    }
}

@Composable
private fun SpeedButton(speed: Float, onSelect: (Float) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        Column(
            Modifier.widthIn(min = 112.dp).clip(RoundedCornerShape(20.dp)).clickable { open = true }.padding(8.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Box(
                Modifier.size(width = 96.dp, height = 72.dp).clip(RoundedCornerShape(20.dp)).background(TeumColors.SecondaryButton),
                contentAlignment = Alignment.Center,
            ) {
                Text(Speeds.label(speed), fontSize = 24.sp, fontWeight = FontWeight.Bold, color = TeumColors.Text)
            }
            Spacer(Modifier.height(6.dp))
            Text("배속", fontSize = 18.sp, color = TeumColors.Icon)
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            Speeds.options.forEach { s ->
                DropdownMenuItem(
                    text = {
                        Text(
                            Speeds.label(s), fontSize = 24.sp,
                            fontWeight = if (s == speed) FontWeight.Bold else FontWeight.Normal,
                            color = if (s == speed) TeumColors.Primary else TeumColors.Text,
                        )
                    },
                    onClick = { onSelect(s); open = false },
                    modifier = Modifier.height(64.dp),
                )
            }
        }
    }
}

@Composable
private fun TextAction(label: String, onClick: () -> Unit) {
    Text(
        label, fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = TeumColors.Primary,
        modifier = Modifier.clip(RoundedCornerShape(12.dp)).clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 12.dp),
    )
}

fun formatTime(ms: Long): String {
    val total = (ms.coerceAtLeast(0) / 1000).toInt()
    return "%d:%02d".format(total / 60, total % 60)
}
