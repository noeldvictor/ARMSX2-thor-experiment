// SPDX-FileCopyrightText: 2026 ARMSX2 Contributors
// SPDX-License-Identifier: GPL-3.0+

#pragma once

#include "GS/GS.h"
#include "GS/GSRegs.h"
#include "GS/GSVector.h"

// The destination-alpha flag, set and cleared by the render-output unit instead of by reading the
// render target.
//
// A PS2 game can use bit 7 of the frame's alpha as a one-bit stencil: a draw with FBMSK 0x7FFFFFFF
// (every bit masked except alpha bit 7) marks pixels, a DATE draw then tests the flag, and a second
// masked draw clears it. Okage: Shadow King does this three times for each of the ~350 strips of
// every character shadow. A mask that keeps some bits of a channel and not others is emulated in the
// fragment shader, which has to read the target; on a device whose texture barriers are off, that
// read is a render-pass break plus a copy of the target, so each strip costs two of them for the
// flag alone. On the Thor's Adreno 740 that is ~17 ms of GS time a frame in one house scene.
//
// Setting or clearing one bit while keeping the others is a bitwise operation, and the colour
// output stage has one: Vulkan logic ops, which work on the stored bits of a UNORM attachment.
// With the shader's alpha forced to exactly 0x80 (FBA) and colour writes limited to alpha:
//   set   = OR:           dst | 0x80
//   clear = AND_INVERTED: ~0x80 & dst
// Both keep bits 0..6 of the stored alpha, which the following DATE draw uses as its blend factor.
// The shader's colour is black, so even a driver that ignores the colour write mask while depth
// testing (Adreno, see m_broken_colormask_with_depth) leaves RGB untouched: 0 | d = d and ~0 & d = d.
//
// Whether a device takes this road is GSDevice::FeatureSupport::alpha_bit_logic_op; whether a draw
// does is Classify, from the registers and the vertex trace.
namespace GSAlphaBitLogicOp
{
	enum : u8
	{
		None = 0,
		SetBit = 1, ///< VK_LOGIC_OP_OR with source alpha 0x80.
		ClearBit = 2, ///< VK_LOGIC_OP_AND_INVERTED with source alpha 0x80.
	};

	// Vulkan (the only backend that builds the logic-op pipeline state), the logicOp device feature,
	// and texture barriers off: that is where the emulated mask costs a pass break and a copy. With
	// barriers on the read stays in the tile and nothing needs to change.
	constexpr bool DeviceQualifies(RenderAPI api, bool logic_op_feature, bool texture_barrier)
	{
		return api == RenderAPI::Vulkan && logic_op_feature && !texture_barrier;
	}

	// The flag draw's shape: a 32-bit frame where only alpha bit 7 is writable, untextured, no fog,
	// no AA1, no blending, no destination alpha test, and an alpha test that cannot fail. The vertex
	// colour must be black with alpha 0 or 0x80 on every vertex, so the bit the draw writes is the
	// same everywhere and the shader's alpha (after FBA) is exactly 0x80. Returns None when the draw
	// is anything else.
	inline u8 Classify(const GIFRegPRIM& prim, const GIFRegFRAME& frame, const GIFRegTEST& test,
		const GIFRegFBA& fba, const GSVector4i& color_min, const GSVector4i& color_max)
	{
		if (frame.PSM != PSMCT32 || frame.FBMSK != 0x7FFFFFFFu)
			return None;
		if (prim.TME || prim.FGE || prim.AA1 || prim.ABE)
			return None;
		if (test.DATE || (test.ATE && test.ATST != ATST_ALWAYS))
			return None;

		// Constant colour across the draw: black, with the alpha's low seven bits clear.
		if (!color_min.eq(color_max))
			return None;
		if (color_min.r != 0 || color_min.g != 0 || color_min.b != 0 || (color_min.a & 0x7F) != 0)
			return None;

		return (fba.FBA || color_min.a >= 0x80) ? SetBit : ClearBit;
	}

	// The DATE draw between a mark and a clear is the other half of the cost. Stencil DATE copies
	// the test result into the stencil buffer before the draw, reading the target in a pass of its
	// own - so each strip still breaks the render pass once. That copy stays true across draws as
	// long as every draw that writes the target's alpha keeps it true, and the flag draws can: they
	// know which bit they leave behind, so the same pipeline also writes the stencil (REPLACE,
	// where the depth test passes, exactly where the logic op writes). The Vulkan backend therefore
	// builds the copy once over the whole target and keeps it for the rest of the render pass;
	// any other alpha write, any stencil clear and the end of the pass drop it.
	//
	// DepthStencilSelector::alpha_bit_stencil is what a flag draw writes into the copy.
	enum : u8
	{
		StencilKeep = 0,
		StencilWriteZero = 1, ///< VK_STENCIL_OP_REPLACE with reference 0: the DATE test now fails here.
		StencilWriteOne = 2, ///< VK_STENCIL_OP_REPLACE with reference 1: the DATE test now passes here.
	};

	// The copy holds 1 where the test passes: alpha bit 7 == DATM.
	constexpr u8 StencilWriteFor(u8 logic_op, bool datm)
	{
		return ((logic_op == SetBit) == datm) ? StencilWriteOne : StencilWriteZero;
	}

	// Whether a DATE draw leaves every pixel it writes passing, so the copy is as true after it as
	// before. It only writes where the test passed, so it is enough that the alpha it writes keeps
	// bit 7 at DATM: no alpha write, or an alpha range (after FBA / fixed coverage alpha) wholly on
	// the passing side. Okage's shadow draw writes alpha 0 under DATM 0.
	constexpr bool KeepsDATEResult(bool datm, bool alpha_write, int alpha_min, int alpha_max, bool alpha_forced_one)
	{
		if (!alpha_write)
			return true;
		return datm ? (alpha_forced_one || alpha_min >= 0x80) : (!alpha_forced_one && alpha_max < 0x80);
	}
} // namespace GSAlphaBitLogicOp
