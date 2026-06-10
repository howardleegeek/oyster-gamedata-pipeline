plugins {
    id("fabric-loom") version "1.7-SNAPSHOT"
    kotlin("jvm") version "1.9.24"
}

version = "0.4.0"
group = "ai.oyster.recorder"

base {
    archivesName.set("oyster-recorder-mod")
}

repositories {
    mavenCentral()
}

dependencies {
    minecraft("com.mojang:minecraft:1.21.1")
    mappings("net.fabricmc:yarn:1.21.1+build.3:v2")
    modImplementation("net.fabricmc:fabric-loader:0.15.11")
    modImplementation("net.fabricmc.fabric-api:fabric-api:0.102.0+1.21.1")

    implementation(kotlin("stdlib"))
    implementation(kotlin("reflect"))

    // Test dependencies
    testImplementation(kotlin("test"))
    testImplementation("org.mockito:mockito-core:5.12.0")
    testImplementation("org.mockito.kotlin:mockito-kotlin:5.4.0")
    testImplementation("org.junit.jupiter:junit-jupiter:5.10.3")
}

tasks.test {
    useJUnitPlatform()
}

kotlin {
    jvmToolchain(21)
}

tasks.withType<org.jetbrains.kotlin.gradle.tasks.KotlinCompile> {
    kotlinOptions {
        jvmTarget = "21"
    }
}

loom {
    runs {
        // Configure the existing 'client' run config (loom creates it by default)
        configureEach {
            ideConfigGenerated(true)
            runDir("run")
        }
    }
}
