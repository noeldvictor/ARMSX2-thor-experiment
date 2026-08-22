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

/** Ordinals of the native `GSTextureUpscaleAlgorithm` enum, which is APPEND ONLY. Only the
 *  filters with a kernel are listed; the native side declines anything else and leaves the
 *  texture native, so offering them here would be a menu of no-ops. */
private val ALGORITHM_ORDINALS = listOf(20, 0, 22, 1, 21, 2, 3, 4, 5, 6, 7, 8, 10, 16, 17, 18, 19)

private val ALGORITHM_LABELS = listOf(
    // Ordered softest-to-sharpest within the resample family, then edge-directed, then
    // neural - which is how someone auditions filters, and not the enum's historical order.
    "Nearest", "Bilinear", "Sharp Bilinear", "Bicubic", "Mitchell", "Lanczos", "Lanczos + CAS",
    "Scale2x", "Eagle", "SuperEagle", "2xSaI", "Super2xSaI", "xBR",
    "Anime4K (model)", "FSRCNN (model)", "SESR (model)", "ESPCN (model)",
)

private val ALGORITHM_DESCRIPTION_KEYS = listOf(
    "renderer.textureUpscale.algorithm.nearest",
    "renderer.textureUpscale.algorithm.bilinear",
    "renderer.textureUpscale.algorithm.sharpbilinear",
    "renderer.textureUpscale.algorithm.bicubic",
    "renderer.textureUpscale.algorithm.mitchell",
    "renderer.textureUpscale.algorithm.lanczos",
    "renderer.textureUpscale.algorithm.lanczoscas",
    "renderer.textureUpscale.algorithm.scale2x",
    "renderer.textureUpscale.algorithm.eagle",
    "renderer.textureUpscale.algorithm.supereagle",
    "renderer.textureUpscale.algorithm.sai2x",
    "renderer.textureUpscale.algorithm.supersai2x",
    "renderer.textureUpscale.algorithm.xbr",
    "renderer.textureUpscale.algorithm.neural",
    "renderer.textureUpscale.algorithm.neural",
    "renderer.textureUpscale.algorithm.neural",
    "renderer.textureUpscale.algorithm.neural",
)

private val SCALE_LABELS = listOf("2x", "4x")

/** Config stores the factor itself (2 or 4), not an index, so the two need converting at the
 *  edge rather than letting an index leak into the settings file. */
private fun scaleToIndex(scale: Int): Int = if (scale >= 4) 1 else 0

private fun indexToScale(index: Int): Int = if (index == 1) 4 else 2

private fun algorithmToIndex(ordinal: Int): Int =
    ALGORITHM_ORDINALS.indexOf(ordinal).let { if (it < 0) ALGORITHM_ORDINALS.indexOf(4) else it }

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
            val worldIndex = algorithmToIndex(worldAlgorithm)
            IntSliderRow(
                str("renderer.textureUpscale.algorithm.label"),
                worldIndex,
                min = 0,
                max = ALGORITHM_LABELS.size - 1,
                description = str(ALGORITHM_DESCRIPTION_KEYS[worldIndex]),
                valueFormatter = { ALGORITHM_LABELS[it.coerceIn(0, ALGORITHM_LABELS.size - 1)] },
            ) {
                onWorldAlgorithmChange(ALGORITHM_ORDINALS[it.coerceIn(0, ALGORITHM_ORDINALS.size - 1)])
            }
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
            val uiIndex = algorithmToIndex(uiAlgorithm)
            IntSliderRow(
                str("renderer.textureUpscale.algorithm.label"),
                uiIndex,
                min = 0,
                max = ALGORITHM_LABELS.size - 1,
                description = str(ALGORITHM_DESCRIPTION_KEYS[uiIndex]),
                valueFormatter = { ALGORITHM_LABELS[it.coerceIn(0, ALGORITHM_LABELS.size - 1)] },
            ) {
                onUiAlgorithmChange(ALGORITHM_ORDINALS[it.coerceIn(0, ALGORITHM_ORDINALS.size - 1)])
            }
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
