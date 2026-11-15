package ai.teum.car

import ai.teum.car.ui.FamilyPrompt
import ai.teum.car.ui.LoadErrorScreen
import ai.teum.car.ui.LoadingScreen
import ai.teum.car.ui.DeepListenScreen
import ai.teum.car.ui.HomeScreen
import ai.teum.car.ui.MainViewModel
import ai.teum.car.ui.PickScreen
import ai.teum.car.ui.PlayerScreen
import ai.teum.car.ui.RoutineScreen
import ai.teum.car.ui.Screen
import ai.teum.car.ui.TeumTheme
import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Snackbar
import androidx.compose.material3.Text
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.delay

class MainActivity : ComponentActivity() {
    private val vm: MainViewModel by viewModels()

    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        vm.onPermissionsChanged()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) requestVehiclePermissions()
        setContent {
            TeumTheme {
                val state by vm.state.collectAsStateWithLifecycle()
                BackHandler(enabled = state.screen != Screen.Home) { vm.goHome() }
                Box(Modifier.fillMaxSize()) {
                    when (state.screen) {
                        Screen.Home -> HomeScreen(state, vm)
                        Screen.Pick -> PickScreen(state, vm)
                        Screen.Loading -> LoadingScreen(state, vm)
                        Screen.LoadError -> LoadErrorScreen(state, vm)
                        Screen.Player -> PlayerScreen(state, vm)
                        Screen.Routine -> RoutineScreen(state, vm)
                        Screen.Deep -> DeepListenScreen(state, vm)
                    }
                    if (state.familyPrompt) FamilyPrompt(vm)
                    state.message?.let { msg ->
                        LaunchedEffect(msg) {
                            delay(3_000)
                            vm.consumeMessage()
                        }
                        Snackbar(Modifier.align(Alignment.BottomCenter).padding(24.dp)) {
                            Text(msg, fontSize = 20.sp)
                        }
                    }
                }
            }
        }
    }

    /** 위치(날씨 브리핑)·좌석(동승자)·내비(남은 시간). 거절해도 기존 동작으로 쓸 수 있다. */
    private fun requestVehiclePermissions() {
        val needed = VEHICLE_PERMISSIONS.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (needed.isNotEmpty()) permissions.launch(needed.toTypedArray())
    }

    private companion object {
        val VEHICLE_PERMISSIONS = listOf(
            Manifest.permission.ACCESS_COARSE_LOCATION,
            "pleos.car.permission.FUSED_LOCATION",
            "pleos.car.permission.CAR_SEATS",
            "pleos.car.permission.NAVI_ROUTE",
        )
    }
}
