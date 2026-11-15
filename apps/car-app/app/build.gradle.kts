plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

val serverOrigin = (findProperty("teum.serverOrigin") as String?) ?: "http://10.0.2.2:8780"

android {
    namespace = "ai.teum.car"
    compileSdk { version = release(36) { minorApiLevel = 1 } }

    defaultConfig {
        applicationId = "com.hiddentrack.onair"
        minSdk = 28
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0-mvp"
        buildConfigField("String", "SERVER_ORIGIN", "\"$serverOrigin\"")
        buildConfigField("String", "API_PREFIX", "\"/api/v1\"")
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    // CarUxRestrictions(주행 중 편집 잠금). 차량에서는 시스템 라이브러리, 폰에서는 없음(required=false).
    useLibrary("android.car")

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildTypes {
        release { isMinifyEnabled = false }
    }

    testOptions { unitTests.isReturnDefaultValues = true }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2025.10.01")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.activity:activity-compose:1.11.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.9.4")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.4")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-guava:1.10.2")
    implementation("androidx.media3:media3-exoplayer:1.8.0")
    implementation("androidx.media3:media3-session:1.8.0")
    implementation("ai.pleos.playground:NaviHelper:3.0.5")
    implementation("ai.pleos.playground:Vehicle:2.3.2")
    implementation("ai.pleos.playground:FusedLocation:1.1.3")
    implementation("ai.pleos.playground:TextToSpeech:3.3.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
