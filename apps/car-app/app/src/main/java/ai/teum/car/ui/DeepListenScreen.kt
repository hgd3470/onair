package ai.teum.car.ui

import ai.teum.car.data.Durations
import ai.teum.car.data.PlaylistItem
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.LineBreak
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// ---------------- 깊이 듣기: 두 사람이 나누는 팟캐스트 (홈 '깊이 듣기' 타일에서 들어온다) ----------------

private val HostB = Color(0xFF8A78C4)

@Composable
fun DeepListenScreen(state: UiState, vm: MainViewModel) {
    Column(Modifier.fillMaxSize().background(TeumColors.Background)) {
        Row(Modifier.fillMaxWidth().padding(start = 24.dp, top = 16.dp, end = 24.dp), verticalAlignment = Alignment.CenterVertically) {
            CircleIconButton(TeumIcons.Back, "이전", onClick = vm::goHome)
            Spacer(Modifier.width(20.dp))
            Text("깊이 듣기", fontSize = 30.sp, fontWeight = FontWeight.Bold, color = TeumColors.Text)
            Spacer(Modifier.width(16.dp))
            Text("두 사람이 나누는 팟캐스트 · 한 편 8~9분", fontSize = 20.sp, color = TeumColors.SubText)
        }
        val items = state.longform.take(4)
        when {
            items.isNotEmpty() -> Column(
                Modifier.weight(1f).fillMaxWidth().padding(horizontal = 32.dp, vertical = 24.dp),
                verticalArrangement = Arrangement.spacedBy(20.dp),
            ) {
                items.chunked(2).forEach { pair ->
                    Row(Modifier.weight(1f).fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                        pair.forEach { item ->
                            EpisodeCard(item, playing = state.current?.contentId == item.contentId, Modifier.weight(1f).fillMaxHeight()) {
                                vm.playLongform(item)
                            }
                        }
                        if (pair.size == 1) Spacer(Modifier.weight(1f))
                    }
                }
            }
            state.longformLoading -> Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = TeumColors.Primary, modifier = Modifier.size(56.dp))
            }
            else -> Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("준비된 깊이 듣기가 없어요", fontSize = 24.sp, color = TeumColors.SubText)
                    Spacer(Modifier.height(16.dp))
                    SecondaryAction("다시 불러오기") { vm.refreshLongform() }
                }
            }
        }
    }
}

@Composable
private fun EpisodeCard(item: PlaylistItem, playing: Boolean, modifier: Modifier, onPlay: () -> Unit) {
    val shape = RoundedCornerShape(24.dp)
    Box(
        modifier.clip(shape).background(if (playing) TeumColors.AccentSurface else TeumColors.Panel)
            .border(if (playing) 2.dp else 1.dp, if (playing) TeumColors.EntryPurple else TeumColors.CardBorder, shape)
            .clickable(onClick = onPlay)
            .padding(36.dp),
    ) {
        Column(Modifier.fillMaxSize().padding(end = 132.dp), verticalArrangement = Arrangement.SpaceBetween) {
            HostAvatars(item.subtitle, size = 96)
            Column {
                Text(
                    item.title, fontSize = 34.sp, lineHeight = 44.sp, fontWeight = FontWeight.Bold, color = TeumColors.Text,
                    maxLines = 2, overflow = TextOverflow.Ellipsis,
                    style = TextStyle(lineBreak = LineBreak(LineBreak.Strategy.Balanced, LineBreak.Strictness.Strict, LineBreak.WordBreak.Phrase)),
                )
                Spacer(Modifier.height(12.dp))
                Text(item.subtitle, fontSize = 24.sp, fontWeight = FontWeight.Medium, color = TeumColors.EntryPurple, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(4.dp))
                Text(Durations.approxMinutes(item.durationSec), fontSize = 22.sp, fontWeight = FontWeight.SemiBold, color = TeumColors.Primary)
            }
        }
        Box(
            Modifier.align(Alignment.BottomEnd).size(104.dp).clip(CircleShape).background(TeumColors.EntryPurple),
            contentAlignment = Alignment.Center,
        ) {
            Icon(if (playing) TeumIcons.Pause else TeumIcons.Play, "${item.title} 재생", tint = Color.White, modifier = Modifier.size(52.dp))
        }
    }
}

/** 두 진행자를 겹친 원 두 개로 보인다. hosts 는 "시사 기자 × 뉴스 안 보는 친구" 꼴. */
@Composable
fun HostAvatars(hosts: String, size: Int, showNames: Boolean = true) {
    val names = hosts.split("×").map { it.trim() }.filter { it.isNotEmpty() }
    val a = names.getOrNull(0) ?: "A"
    val b = names.getOrNull(1) ?: "B"
    Box(Modifier.width((size * 2 - size / 8).dp).height(size.dp)) {
        Avatar(if (showNames) a else "", TeumColors.EntryPurple, size, Modifier)
        Avatar(if (showNames) b else "", HostB, size, Modifier.offset(x = (size - size / 8).dp))
    }
}

@Composable
private fun Avatar(name: String, color: Color, size: Int, modifier: Modifier) {
    Box(
        modifier.size(size.dp).clip(CircleShape).background(Color.White).padding(3.dp).clip(CircleShape).background(color),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            shortName(name), fontSize = (size * 0.19f).sp, fontWeight = FontWeight.Bold, color = Color.White,
            textAlign = TextAlign.Center, maxLines = 1,
        )
    }
}

/** "뉴스 안 보는 친구" → "친구", "헬스 트레이너" → "트레이너". 동그라미 안에 들어갈 마지막 낱말. */
private fun shortName(name: String): String =
    name.replace("'", "").split(" ").lastOrNull { it.isNotBlank() }?.take(4) ?: name.take(2)
