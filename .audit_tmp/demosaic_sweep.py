#!/usr/bin/env python3
"""Sweep demosaic algorithms to see if any reduces ΔE vs dng_validate."""
import sys, warnings
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/.audit_tmp")
import rawpy, tifffile, colour
import adobe_pipeline as ap
from lrt_cinema.dcp import parse_dcp

D65_xy = np.array([0.31270, 0.32900])

def to_lab(arr):
    linear = colour.models.eotf_sRGB(arr.astype(np.float64) / 255.0)
    xyz = colour.RGB_to_XYZ(linear, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=D65_xy)

def measure(srgb_ours, ref_uint16, label):
    ref_8 = (ref_uint16.astype(np.float32) / 65535.0 * 255).astype(np.uint8)
    oh, ow = srgb_ours.shape[:2]; th, tw = ref_uint16.shape[:2]
    cy = (oh - th) // 2; cx = (ow - tw) // 2
    crop = srgb_ours[cy:cy+th, cx:cx+tw]
    de = colour.delta_E(to_lab(crop), to_lab(ref_8), method='CIE 2000')
    print(f"  {label:<14} mean={de.mean():.3f} P50={np.percentile(de,50):.3f} <1%={(de<1).mean()*100:.1f}%")

def demosaic_with_algo(raw_path, algo):
    with rawpy.imread(str(raw_path)) as raw:
        rgb = raw.postprocess(
            output_bps=16, gamma=(1, 1), no_auto_bright=True,
            use_camera_wb=False, use_auto_wb=False,
            user_wb=[1.0, 1.0, 1.0, 1.0],
            output_color=rawpy.ColorSpace.raw,
            demosaic_algorithm=algo,
            half_size=False, four_color_rgb=False,
            highlight_mode=rawpy.HighlightMode.Clip,
        )
    return rgb.astype(np.float32) / 65535.0

ALGOS = {
    "AHD": rawpy.DemosaicAlgorithm.AHD,
    "VNG": rawpy.DemosaicAlgorithm.VNG,
    "PPG": rawpy.DemosaicAlgorithm.PPG,
    "LINEAR": rawpy.DemosaicAlgorithm.LINEAR,
}
# Try DCB if available
try:
    ALGOS["DCB"] = rawpy.DemosaicAlgorithm.DCB
except AttributeError:
    pass
# Try AMaZE / DHT / AAHD if available — skip GPL3-required ones
# (AMaZE, AFD, VCD, LMMSE, AMAZE_VCD, MODIFIED_AHD, DHT need GPL3 demosaic pack)
for name in ["DHT", "AAHD"]:
    try:
        algo_val = getattr(rawpy.DemosaicAlgorithm, name)
        # Probe — some algos require GPL3 pack
        try:
            with rawpy.imread('/tmp/dng_out/DSC_4053.dng') as r:
                r.postprocess(output_bps=8, demosaic_algorithm=algo_val,
                              half_size=True, no_auto_bright=True)
            ALGOS[name] = algo_val
        except Exception:
            pass
    except AttributeError:
        pass

print("\nAvailable demosaic algos:", list(ALGOS.keys()))

# Gym
print("\nGym (Camera Standard):")
gym_dng = '/tmp/dng_out/DSC_4053.dng'
gym_dcp = '/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/Nikon D750 Camera Standard.dcp'
gym_ref = tifffile.imread('/tmp/dng_out/DSC_4053_dngvalidate.tif')
gym_profile = parse_dcp(gym_dcp)
with rawpy.imread(gym_dng) as raw:
    gym_asn = 1.0 / np.array(raw.camera_whitebalance[:3], dtype=np.float32); gym_asn = gym_asn / gym_asn[1]
gym_be = ap.read_dng_baseline_exposure(gym_dng)
gym_dbr = ap.read_dcp_default_black_render(gym_dcp)

ap.APPLY_LOOKTABLE = True; ap.APPLY_TONECURVE = True
for name, algo in ALGOS.items():
    cam = demosaic_with_algo(gym_dng, algo)
    pp = ap.apply_adobe_pipeline(cam, gym_profile, gym_asn, 5500.0,
                                  dng_baseline_exposure=gym_be,
                                  default_black_render=gym_dbr)
    srgb = ap.prophoto_to_srgb(pp)
    measure(srgb, gym_ref, name)

# Rose
print("\nRose (Adobe Standard):")
rose_dng = '/tmp/dng_out/rose.dng'
rose_dcp = '/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Adobe Standard/Nikon D750 Adobe Standard.dcp'
rose_ref = tifffile.imread('/tmp/dng_out/rose_dngval_Camera_Standard.tif')
rose_profile = parse_dcp(rose_dcp)
with rawpy.imread(rose_dng) as raw:
    rose_asn = 1.0 / np.array(raw.camera_whitebalance[:3], dtype=np.float32); rose_asn = rose_asn / rose_asn[1]
rose_be = ap.read_dng_baseline_exposure(rose_dng)
rose_dbr = ap.read_dcp_default_black_render(rose_dcp)

for name, algo in ALGOS.items():
    cam = demosaic_with_algo(rose_dng, algo)
    pp = ap.apply_adobe_pipeline(cam, rose_profile, rose_asn, 5500.0,
                                  dng_baseline_exposure=rose_be,
                                  default_black_render=rose_dbr)
    srgb = ap.prophoto_to_srgb(pp)
    measure(srgb, rose_ref, name)
