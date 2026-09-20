package com.armsx2.ui.common

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.armsx2.i18n.str
import com.armsx2.ui.settings.IntSliderRow
import com.armsx2.ui.settings.SegmentedRow
import com.armsx2.ui.settings.SettingsDivider
import com.armsx2.ui.settings.ToggleRow
import com.armsx2.ui.settings.controllerFocusable

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
// RAISR-HD: kernels fit on HD texture packs, bundled in the APK. First because it is the
// default and the one entry that needs no explanation to pick.
private val FAMILY_LEARNED = listOf(24)
private val LABELS_LEARNED = listOf("RAISR-HD")

private val FAMILY_RESAMPLE = listOf(20, 0, 22, 1, 21, 2, 3)
private val LABELS_RESAMPLE = listOf("Nearest", "Bilinear", "Sharp", "Bicubic", "Mitchell", "Lanczos", "L+CAS")

private val FAMILY_PIXELART = listOf(4, 5, 6, 7, 8, 14)
private val LABELS_PIXELART = listOf("Scale2x", "Eagle", "SuperEagle", "2xSaI", "S2xSaI", "MMPX")

private val FAMILY_EDGE = listOf(10, 11, 23, 16)
private val LABELS_EDGE = listOf("xBR", "xBRZ", "ScaleForce", "Anime4K")

private val FAMILY_NEURAL = listOf(17, 18, 19)
private val LABELS_NEURAL = listOf("FSRCNN", "SESR", "ESPCN")

/** Description key per ordinal. */
private val DESCRIPTION_KEYS = mapOf(
    24 to "renderer.textureUpscale.algorithm.raisrhd",
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
    14 to "renderer.textureUpscale.algorithm.mmpx",
    10 to "renderer.textureUpscale.algorithm.xbr",
    11 to "renderer.textureUpscale.algorithm.xbrz",
    23 to "renderer.textureUpscale.algorithm.scaleforce",
    16 to "renderer.textureUpscale.algorithm.anime4k",
    17 to "renderer.textureUpscale.algorithm.neural",
    18 to "renderer.textureUpscale.algorithm.neural",
    19 to "renderer.textureUpscale.algorithm.neural",
)

/**
 * One family of filters as wrapping chips.
 *
 * FlowRow rather than a single row: seven chips do not fit the width and a plain row
 * silently CLIPS the overflow, so Lanczos, L+CAS and xBR existed but could not be seen or
 * reached. Wrapping is also what lets the labels stay readable words instead of slivers.
 */
@Composable
private fun FamilyChips(
    title: String,
    ordinals: List<Int>,
    labels: List<String>,
    current: Int,
    keyPrefix: String,
    description: String?,
    onChange: (Int) -> Unit,
) {
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        Text(title, style = MaterialTheme.typography.titleSmall)
        if (description != null) {
            Spacer(Modifier.height(4.dp))
            Text(
                description,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Spacer(Modifier.height(8.dp))
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(7.dp),
            verticalArrangement = Arrangement.spacedBy(7.dp),
        ) {
            ordinals.forEachIndexed { index, ordinal ->
                val apply = { onChange(ordinal) }
                FilterChip(
                    selected = current == ordinal,
                    onClick = apply,
                    label = { Text(labels[index]) },
                    shape = RoundedCornerShape(11.dp),
                    modifier = Modifier.controllerFocusable(
                        "$keyPrefix.$ordinal",
                        RoundedCornerShape(11.dp),
                        onConfirm = apply,
                    ),
                )
            }
        }
    }
}

/**
 * Grouped by how each filter DECIDES, which is what actually predicts how it looks:
 *
 *  - **Resample** — weighted averages with no notion of an edge. Soft, never wrong.
 *  - **Pixel art** — exact colour matches and rule tables. Keeps the source palette, so it
 *    holds up on sprites and text where the smooth filters turn letterforms to mush.
 *  - **Edge-directed** — colour *distance* and gradients rather than equality, so gradients
 *    and anti-aliased source art survive instead of being treated as noise.
 *  - **Neural** — a trained model, when one is installed.
 *
 * The usual way these are listed is by vintage (2xSaI, HQx, xBR, ...), which tells you
 * nothing about which to reach for. Only the family owning the current selection has a
 * filled chip, so where you are is readable at a glance.
 */
@Composable
private fun AlgorithmPicker(current: Int, keyPrefix: String, onChange: (Int) -> Unit) {
    val described = DESCRIPTION_KEYS[current]?.let { str(it) }

    FamilyChips(
        str("renderer.textureUpscale.family.learned"), FAMILY_LEARNED, LABELS_LEARNED,
        current, "$keyPrefix.learned",
        if (current in FAMILY_LEARNED) described else null, onChange,
    )
    FamilyChips(
        str("renderer.textureUpscale.family.resample"), FAMILY_RESAMPLE, LABELS_RESAMPLE,
        current, "$keyPrefix.resample",
        if (current in FAMILY_RESAMPLE) described else null, onChange,
    )
    FamilyChips(
        str("renderer.textureUpscale.family.pixelart"), FAMILY_PIXELART, LABELS_PIXELART,
        current, "$keyPrefix.pixelart",
        if (current in FAMILY_PIXELART) described else null, onChange,
    )
    FamilyChips(
        str("renderer.textureUpscale.family.edge"), FAMILY_EDGE, LABELS_EDGE,
        current, "$keyPrefix.edge",
        if (current in FAMILY_EDGE) described else null, onChange,
    )
    FamilyChips(
        str("renderer.textureUpscale.family.neural"), FAMILY_NEURAL, LABELS_NEURAL,
        current, "$keyPrefix.neural",
        if (current in FAMILY_NEURAL) described else str("renderer.textureUpscale.family.neuralHint"),
        onChange,
    )
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
    deposterize: Boolean,
    onWorldEnabledChange: (Boolean) -> Unit,
    onWorldAlgorithmChange: (Int) -> Unit,
    onWorldScaleChange: (Int) -> Unit,
    onUiEnabledChange: (Boolean) -> Unit,
    onUiAlgorithmChange: (Int) -> Unit,
    onUiScaleChange: (Int) -> Unit,
    onVramBudgetChange: (Int) -> Unit,
    onDeposterizeChange: (Boolean) -> Unit,
    /** In-game only: flush the texture cache so every visible texture re-runs through the
     *  current filter. Null where there is no running game (All Settings), and the row is
     *  simply absent. Without it a filter change only reaches textures uploaded afterwards,
     *  which reads as "the setting does nothing". */
    onReloadTextures: (() -> Unit)? = null,
) {
    Column(Modifier.fillMaxWidth()) {
        Text(
            str("renderer.textureUpscale.about"),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 4.dp, vertical = 8.dp),
        )
        if (onReloadTextures != null) {
            OutlinedButton(
                onClick = onReloadTextures,
                shape = RoundedCornerShape(14.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 4.dp, vertical = 4.dp)
                    .controllerFocusable("texUpscale.reload", RoundedCornerShape(14.dp), onConfirm = onReloadTextures),
            ) {
                Text("\u21bb  " + str("renderer.textureUpscale.reload.label"))
            }
            Text(
                str("renderer.textureUpscale.reload.description"),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 4.dp),
            )
            Spacer(Modifier.height(6.dp))
        }

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
            AlgorithmPicker(worldAlgorithm, "texUpscale.world") { onWorldAlgorithmChange(it) }
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
            AlgorithmPicker(uiAlgorithm, "texUpscale.ui") { onUiAlgorithmChange(it) }
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
            // A pre-pass rather than a filter choice, hence a toggle beside the picker instead
            // of another chip: it runs BEFORE whichever filter is selected, so it composes with
            // all of them rather than competing.
            ToggleRow(
                str("renderer.textureUpscale.deposterize.label"),
                deposterize,
                description = str("renderer.textureUpscale.deposterize.description"),
            ) {
                onDeposterizeChange(it)
            }
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
