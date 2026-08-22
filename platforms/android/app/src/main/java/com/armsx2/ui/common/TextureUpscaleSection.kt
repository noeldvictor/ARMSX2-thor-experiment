package com.armsx2.ui.common

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.armsx2.i18n.str
import com.armsx2.ui.settings.IntSliderRow
import com.armsx2.ui.settings.SegmentedRow
import com.armsx2.ui.settings.SettingsDivider
import com.armsx2.ui.settings.ToggleRow

/**
 * Texture upscaling rows: a toggle, algorithm and scale per texture class, and the shared
 * VRAM budget.
 *
 * Presentation only — the caller wires each callback to its OWN settings tier, exactly like
 * [ShaderChainSection] and [LsfgSection]. That is what lets one definition serve both the
 * All Settings tab (which honours the Global / Game scope) and the in-game pause menu. Do
 * NOT reach for a settings tier from in here.
 *
 * ## This is not the screen upscaler
 *
 * It scales each texture at upload time and caches the result, so a texture is scaled once
 * and then costs nothing. That is the opposite of FSR1 / the shader chain, which pay per
 * frame forever. It is also independent of the internal resolution multiplier and of
 * texture packs — a pack wins where one exists, this covers everything else.
 *
 * ## World and UI are separate on purpose
 *
 * A filter that flatters a painted wall will mangle a HUD font, so the two classes have
 * their own enable, algorithm and scale rather than sharing one setting. Classification is
 * by size on the native side (128x128 and under is UI), which is a heuristic — that it can
 * guess wrong is exactly why each class is switchable on its own.
 */

/** Ordinals of the native `GSTextureUpscaleAlgorithm` enum, which is APPEND ONLY, grouped
 *  into the families the menu shows. Only filters with a kernel appear; the native side
 *  declines anything else and leaves the texture native, so listing them would be a menu of
 *  no-ops. Each family's ordinal and label lists must stay the same length - a mismatch
 *  silently selects a different filter than the one named. */
private val FAMILY_RESAMPLE = listOf(20, 0, 22, 1, 21, 2, 3)
private val LABELS_RESAMPLE = listOf("Nearest", "Bilinear", "Sharp", "Bicubic", "Mitchell", "Lanczos", "L+CAS")

private val FAMILY_EDGE = listOf(4, 5, 6, 7, 8, 10)
private val LABELS_EDGE = listOf("Scale2x", "Eagle", "SuperEagle", "2xSaI", "S2xSaI", "xBR")

private val FAMILY_NEURAL = listOf(16, 17, 18, 19)
private val LABELS_NEURAL = listOf("Anime4K", "FSRCNN", "SESR", "ESPCN")

/** Description key per ordinal. */
private val DESCRIPTION_KEYS = mapOf(
    20 to "renderer.textureUpscale.algorithm.nearest",
    0 to "renderer.textureUpscale.algorithm.bilinear",
    22 to "renderer.textureUpscale.algorithm.sharpbilinear",
    1 to "renderer.textureUpscale.algorithm.bicubic",
    21 to "renderer.textureUpscale.algorithm.mitchell",
    2 to "renderer.textureUpscale.algorithm.lanczos",
    3 to "renderer.textureUpscale.algorithm.lanczoscas",
    4 to "renderer.textureUpscale.algorithm.scale2x",
    5 to "renderer.textureUpscale.algorithm.eagle",
    6 to "renderer.textureUpscale.algorithm.supereagle",
    7 to "renderer.textureUpscale.algorithm.sai2x",
    8 to "renderer.textureUpscale.algorithm.supersai2x",
    10 to "renderer.textureUpscale.algorithm.xbr",
    16 to "renderer.textureUpscale.algorithm.neural",
    17 to "renderer.textureUpscale.algorithm.neural",
    18 to "renderer.textureUpscale.algorithm.neural",
    19 to "renderer.textureUpscale.algorithm.neural",
)

/**
 * Three chip rows, one per family, rather than one control listing seventeen filters.
 *
 * Only the family owning the current selection highlights anything; the others pass -1,
 * which SegmentedRow renders as no selection. That is what makes "which family am I in"
 * readable at a glance instead of something you work out from a name.
 */
@Composable
private fun AlgorithmPicker(current: Int, onChange: (Int) -> Unit) {
    val described = DESCRIPTION_KEYS[current]
    SegmentedRow(
        str("renderer.textureUpscale.family.resample"),
        LABELS_RESAMPLE,
        FAMILY_RESAMPLE.indexOf(current),
        description = if (described != null && current in FAMILY_RESAMPLE) str(described) else null,
    ) { onChange(FAMILY_RESAMPLE[it]) }

    SegmentedRow(
        str("renderer.textureUpscale.family.edge"),
        LABELS_EDGE,
        FAMILY_EDGE.indexOf(current),
        description = if (described != null && current in FAMILY_EDGE) str(described) else null,
    ) { onChange(FAMILY_EDGE[it]) }

    SegmentedRow(
        str("renderer.textureUpscale.family.neural"),
        LABELS_NEURAL,
        FAMILY_NEURAL.indexOf(current),
        description = if (described != null && current in FAMILY_NEURAL) str(described)
                      else str("renderer.textureUpscale.family.neuralHint"),
    ) { onChange(FAMILY_NEURAL[it]) }
}

private val SCALE_LABELS = listOf("2x", "4x")

/** Config stores the factor itself (2 or 4), not an index, so the two need converting at the
 *  edge rather than letting an index leak into the settings file. */
private fun scaleToIndex(scale: Int): Int = if (scale >= 4) 1 else 0

private fun indexToScale(index: Int): Int = if (index == 1) 4 else 2

@Composable
fun TextureUpscaleSection(
    worldEnabled: Boolean,
    worldAlgorithm: Int,
    worldScale: Int,
    uiEnabled: Boolean,
    uiAlgorithm: Int,
    uiScale: Int,
    vramBudgetMb: Int,
    onWorldEnabledChange: (Boolean) -> Unit,
    onWorldAlgorithmChange: (Int) -> Unit,
    onWorldScaleChange: (Int) -> Unit,
    onUiEnabledChange: (Boolean) -> Unit,
    onUiAlgorithmChange: (Int) -> Unit,
    onUiScaleChange: (Int) -> Unit,
    onVramBudgetChange: (Int) -> Unit,
) {
    Column(Modifier.fillMaxWidth()) {
        Text(
            str("renderer.textureUpscale.about"),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 4.dp, vertical = 8.dp),
        )

        ToggleRow(
            str("renderer.textureUpscale.world.label"),
            worldEnabled,
            description = str("renderer.textureUpscale.world.description"),
        ) {
            onWorldEnabledChange(it)
        }
        // Gate: the per-class rows only exist while that class is on, matching the shader
        // chain and LSFG sections. It also keeps dead rows out of the controller focus
        // registry rather than parking focus on something inert.
        if (worldEnabled) {
            AlgorithmPicker(worldAlgorithm) { onWorldAlgorithmChange(it) }
            SegmentedRow(
                str("renderer.textureUpscale.scale.label"),
                SCALE_LABELS,
                scaleToIndex(worldScale),
            ) {
                onWorldScaleChange(indexToScale(it))
            }
        }

        SettingsDivider()

        ToggleRow(
            str("renderer.textureUpscale.ui.label"),
            uiEnabled,
            description = str("renderer.textureUpscale.ui.description"),
        ) {
            onUiEnabledChange(it)
        }
        if (uiEnabled) {
            AlgorithmPicker(uiAlgorithm) { onUiAlgorithmChange(it) }
            SegmentedRow(
                str("renderer.textureUpscale.scale.label"),
                SCALE_LABELS,
                scaleToIndex(uiScale),
            ) {
                onUiScaleChange(indexToScale(it))
            }
        }

        // Shared, not per class: the budget is one pool and splitting it would just make two
        // numbers the user has to keep in their head.
        if (worldEnabled || uiEnabled) {
            SettingsDivider()
            IntSliderRow(
                str("renderer.textureUpscale.budget.label"),
                vramBudgetMb,
                min = 64,
                max = 2048,
                description = str("renderer.textureUpscale.budget.description"),
                valueFormatter = { "$it MB" },
            ) {
                onVramBudgetChange(it)
            }
        }
    }
}
