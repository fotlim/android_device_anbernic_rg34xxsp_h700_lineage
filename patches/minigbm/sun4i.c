/*
 * Copyright 2026 The LineageOS RG34XXSP port.
 *
 * Use of this source code is governed by a BSD-style license that can be
 * found in the LICENSE file.
 */

/*
 * Allwinner sun4i/sun8i/sun50i display engine ("sun4i-drm").
 *
 * This backend exists because the display controller is the only DRM device on
 * this SoC that owns memory the display engine can scan out: it is a
 * DRM_GEM_DMA driver, so DRM_IOCTL_MODE_CREATE_DUMB hands back a physically
 * contiguous buffer that can be exported as a dma-buf and imported by anyone
 * else on the system.  The Mali GPU (panfrost) is a render-only node with
 * shmem-backed objects and no dumb buffer support, and EGL here is swiftshader,
 * so the GPU is not in the allocation path at all.
 */

#include <errno.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <xf86drm.h>

#include "drv_priv.h"
#include "helpers.h"
#include "util.h"

/*
 * The display engine handles the usual RGB formats; the frontend/backend pair
 * additionally does YUV, which is what the video decoder hands to the composer.
 */
static const uint32_t scanout_render_formats[] = {
	DRM_FORMAT_ARGB8888, DRM_FORMAT_XRGB8888, DRM_FORMAT_ABGR8888,
	DRM_FORMAT_XBGR8888, DRM_FORMAT_RGB565,   DRM_FORMAT_BGR565,
};

static const uint32_t texture_only_formats[] = {
	DRM_FORMAT_R8, DRM_FORMAT_NV12, DRM_FORMAT_NV21, DRM_FORMAT_YVU420,
	DRM_FORMAT_YVU420_ANDROID,
};

static int sun4i_init(struct driver *drv)
{
	drv_add_combinations(drv, scanout_render_formats, ARRAY_SIZE(scanout_render_formats),
			     &LINEAR_METADATA, BO_USE_RENDER_MASK | BO_USE_SCANOUT);

	drv_add_combinations(drv, texture_only_formats, ARRAY_SIZE(texture_only_formats),
			     &LINEAR_METADATA, BO_USE_TEXTURE_MASK);

	return drv_modify_linear_combinations(drv);
}

static uint32_t sun4i_resolve_format(struct driver *drv, uint32_t format, uint64_t flags)
{
	switch (format) {
	case DRM_FORMAT_FLEX_IMPLEMENTATION_DEFINED:
		/* Same workaround the other software-ish backends carry. */
		return DRM_FORMAT_XBGR8888;
	case DRM_FORMAT_FLEX_YCbCr_420_888:
		return DRM_FORMAT_YVU420;
	default:
		return format;
	}
}

/*
 * drv_dumb_bo_create() asks the kernel for a dumb buffer, which for this driver
 * is drm_sun4i_gem_dumb_create(): it aligns the pitch to 2 (the frontend only
 * takes even pitches for YUV) and allocates a contiguous DMA object.
 */
const struct backend backend_sun4i = {
	.name = "sun4i-drm",
	.init = sun4i_init,
	.bo_create = drv_dumb_bo_create,
	.bo_destroy = drv_dumb_bo_destroy,
	.bo_import = drv_prime_bo_import,
	.bo_map = drv_dumb_bo_map,
	.bo_unmap = drv_bo_munmap,
	.resolve_format = sun4i_resolve_format,
};
