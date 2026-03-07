# -*- coding: utf-8 -*-
# core/image_filters.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple, cast

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps


def _clamp(v: int | float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else float(v)


def _f_from_int(v: int, *, scale: float = 1.0, lo: float = 0.0, hi: float = 3.0) -> float:
    return _clamp(1.0 + _clamp(v, -100, 100) / 100.0 * scale, lo, hi)


@dataclass
class BasicParams:
    brightness:  int = 0
    contrast:    int = 0
    saturation:  int = 0
    sharpness:   int = 0
    temperature: int = 0


def apply_basic(img: Image.Image, p: BasicParams) -> Image.Image:

    im: Image.Image = img.convert("RGB")

    if p.brightness:
        im = cast(Image.Image,
                  ImageEnhance.Brightness(im).enhance(
                      _f_from_int(p.brightness, scale=1.0, lo=0.0, hi=2.0)))
    if p.contrast:
        im = cast(Image.Image,
                  ImageEnhance.Contrast(im).enhance(
                      _f_from_int(p.contrast, scale=1.0, lo=0.0, hi=2.0)))
    if p.saturation:
        im = cast(Image.Image,
                  ImageEnhance.Color(im).enhance(
                      _f_from_int(p.saturation, scale=1.0, lo=0.0, hi=2.0)))
    if p.sharpness:
        im = cast(Image.Image,
                  ImageEnhance.Sharpness(im).enhance(
                      _f_from_int(p.sharpness, scale=2.0, lo=0.0, hi=3.0)))
    if p.temperature:
        t     = _clamp(p.temperature, -100, 100) / 100.0
        r_mul = 1.0 + 0.18 * t
        b_mul = 1.0 - 0.18 * t
        r_lut: list[int] = [min(255, max(0, int(round(i * r_mul)))) for i in range(256)]
        b_lut: list[int] = [min(255, max(0, int(round(i * b_mul)))) for i in range(256)]
        r_ch, g_ch, b_ch = im.split()
        r_ch = cast(Image.Image, r_ch.point(r_lut))
        b_ch = cast(Image.Image, b_ch.point(b_lut))
        im   = Image.merge("RGB", (r_ch, g_ch, b_ch))

    return im


def _blend(a: Image.Image, b: Image.Image, intensity: int) -> Image.Image:
    k = _clamp(intensity, 0, 100) / 100.0
    if k <= 0.0:
        return a
    if k >= 1.0:
        return b
    return cast(Image.Image, Image.blend(a, b, k))


def _screen(a: Image.Image, b: Image.Image) -> Image.Image:
    try:
        return cast(Image.Image, ImageChops.screen(a, b))
    except AttributeError:
        inv_a = cast(Image.Image, ImageChops.invert(a))
        inv_b = cast(Image.Image, ImageChops.invert(b))
        return cast(Image.Image, ImageChops.invert(ImageChops.multiply(inv_a, inv_b)))


def style_names() -> Tuple[str, ...]:
    return ("none", "grayscale", "sepia", "vintage")


def pro_names() -> Tuple[str, ...]:
    return ("none", "vignette", "clarity", "grain", "fade", "glow")


def apply_style(img: Image.Image, name: str, intensity: int) -> Image.Image:
    im: Image.Image = img.convert("RGB")
    name = (name or "none").strip().lower()
    if name == "none" or intensity <= 0:
        return im

    if name == "grayscale":
        return _blend(im, cast(Image.Image, ImageOps.grayscale(im).convert("RGB")), intensity)

    if name == "sepia":
        g: Image.Image = cast(Image.Image, ImageOps.grayscale(im))
        s: Image.Image = cast(Image.Image,
                               ImageOps.colorize(g, black="#3b2a1a", white="#e6d3b1").convert("RGB"))
        return _blend(im, s, intensity)

    if name == "vintage":
        k   = _clamp(intensity, 0, 100) / 100.0
        out: Image.Image = cast(Image.Image,
                                 ImageEnhance.Color(im).enhance(1.0 - 0.35 * k))
        out = cast(Image.Image,
                   ImageEnhance.Contrast(out).enhance(1.0 - 0.20 * k))
        out = apply_basic(out, BasicParams(temperature=int(60 * k)))
        white: Image.Image = Image.new("RGB", out.size, (255, 255, 255))
        return cast(Image.Image, Image.blend(out, white, 0.10 * k))

    return im


def apply_pro(img: Image.Image, name: str, intensity: int) -> Image.Image:
    im: Image.Image = img.convert("RGB")
    name = (name or "none").strip().lower()
    if name == "none" or intensity <= 0:
        return im

    k = _clamp(intensity, 0, 100) / 100.0

    if name == "vignette":
        w, h   = im.size
        margin = int(min(w, h) * (0.10 + 0.10 * (1.0 - k)))
        mask: Image.Image = Image.new("L", (w, h), 255)
        dr = ImageDraw.Draw(mask)
        dr.ellipse((margin, margin, w - margin, h - margin), fill=0)
        blur_r = max(1, int(min(w, h) * (0.08 + 0.10 * k)))
        mask   = cast(Image.Image, mask.filter(ImageFilter.GaussianBlur(radius=blur_r)))
        mask   = cast(Image.Image, ImageOps.invert(mask))
        mask   = cast(Image.Image, mask.point([int((1.0 - k) * 255 + k * i) for i in range(256)]))
        v: Image.Image = Image.merge("RGB", (mask, mask, mask))
        return cast(Image.Image, ImageChops.multiply(im, v))

    if name == "clarity":
        radius    = 2 + int(2 * k)
        percent   = 120 + int(180 * k)
        threshold = 2 + int(4 * k)
        return cast(Image.Image,
                    im.filter(ImageFilter.UnsharpMask(
                        radius=radius, percent=percent, threshold=threshold)))

    if name == "grain":
        try:
            sigma: int     = int(10 + 30 * k)
            noise: Image.Image = cast(Image.Image,
                                      Image.effect_noise(im.size, sigma).convert("L"))
            noise_rgb: Image.Image = cast(Image.Image,
                                          ImageOps.colorize(
                                              noise, black="#808080", white="#c0c0c0"
                                          ).convert("RGB"))
            return cast(Image.Image, Image.blend(im, noise_rgb, 0.18 * k))
        except Exception:
            return im

    if name == "fade":
        white2: Image.Image = Image.new("RGB", im.size, (255, 255, 255))
        out2: Image.Image   = cast(Image.Image, Image.blend(im, white2, 0.25 * k))
        return cast(Image.Image,
                    ImageEnhance.Contrast(out2).enhance(1.0 - 0.15 * k))

    if name == "glow":
        blur_r2 = 2 + 6 * k
        blur2: Image.Image = cast(Image.Image,
                                   im.filter(ImageFilter.GaussianBlur(radius=blur_r2)))
        return _screen(im, blur2)

    return im
