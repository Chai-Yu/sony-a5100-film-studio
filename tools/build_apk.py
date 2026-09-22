#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Original additions only; underlying third-party rights remain separate. See LICENSING.md and NOTICE.
"""Reproducible local alpha patch of the verified Ricoh v1.1.4 APK.

The input Sony-derived APK is supplied separately. No firmware is modified.
The result uses a separate same-length package name and a private signing key.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from axml_strings import replace_string as axml_replace_string
from sign_apk import sign_apk, ensure_pem
from movie_menu import LABELS as MOVIE_LABELS, patch_movie_menu
from filter_strength import STRENGTHS, LABELS as STRENGTH_LABELS, blend_profile, patch_strength_menu, strength_methods
from film_profiles import (EXPECTED_HOOK, LEICA_FILE, NATIVE_EFFECTS, NATIVE_MODES,
                           combined_profiles, leica_profiles)

OLD = 'com.sony.imaging.app.pictureeffectplus'
NEW = 'com.yuki.imaging.app.pictureeffectplus'
HOOK = 'L'+OLD.replace('.','/')+'/shooting/camera/RicohHook;'
CTRL = 'L'+OLD.replace('.','/')+'/shooting/camera/PictureEffectPlusController;'
EXPECTED = '80cb4a541f5f3dd49e8f53ffb1905048097fec17209fc9cb595a00681e65e8ea'
VERSION = '0.2.2-alpha'
# versionName carried by the Sony base APK. It is replaced with ANDROID_VERSION
# below, and the two differ in length: a byte substitution would silently
# invalidate every string offset after it, so the manifest goes through
# axml_strings instead.
ORIGINAL_ANDROID_VERSION = '1.31'
ANDROID_VERSION = '0.2.2'
APP_NAME = '胶片工坊'
# Alpha of the optional dark background behind the filter chooser's list panel.
# Default 0 = fully transparent, which is what the live view needs: the text
# elements of this layout (menu_item_name 484px, guide_view 484px wide) are laid
# across the 280px preview column, so any background behind them also tints the
# live view. --menu-scrim 0xb0 paints the panel (and dims the preview with it).
DEFAULT_MENU_SCRIM = 0x00

def replace_method(text, signature, replacement):
    pattern = r'^\.method [^\n]*'+re.escape(signature)+r'\n[\s\S]*?^\.end method'
    text, n = re.subn(pattern, lambda _: replacement, text, count=1, flags=re.M)
    if n != 1:
        raise ValueError('Expected exactly one method: '+signature)
    return text

def quote(s):
    return json.dumps(s,ensure_ascii=True)
def lookup_method(name, profiles, kind, movie=False):
    ret = {'gamma':'[B','matrix':'[I','name':'Ljava/lang/String;',
           'guide':'Ljava/lang/String;'}[kind]
    lines=[f'.method public static {name}(Ljava/lang/String;){ret}', '    .locals 2',
           '    if-eqz p0, :none']
    if kind in ('gamma', 'matrix'):
        lines += [f'    invoke-static {{}}, {HOOK}->getStrength()I', '    move-result v1']
    for i,p in enumerate(profiles):
        lines += [f'    const-string v0, {quote(p["id"])}',
                  '    invoke-virtual {v0, p0}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z',
                  '    move-result v0', f'    if-eqz v0, :next_{i}']
        if kind in ('gamma','matrix'):
            for strength in STRENGTHS[:-1]:
                lines += [f'    const/16 v0, {hex(strength)}',
                          f'    if-ne v1, v0, :strength_{i}_{strength}',
                          f'    sget-object v0, {HOOK}->sFuji{kind}{i}_{strength}:{ret}',
                          '    return-object v0', f'    :strength_{i}_{strength}']
            lines += [f'    sget-object v0, {HOOK}->sFuji{kind}{i}_100:{ret}']
        else:
            value=p['name'] if kind=='name' else p['guide']
            lines += [f'    const-string v0, {quote(value)}']
        lines += ['    return-object v0',f'    :next_{i}']
    if kind in ('name', 'guide'):
        labels_map = {'ApplicationTop': ('胶片风格', '富士参考与理光风格；拍照和录像待机均可切换。')}
        labels_map.update(STRENGTH_LABELS)
        labels_map.update(RAW_LABELS)
        if movie:
            labels_map.update(MOVIE_LABELS)
        for i, (key, labels) in enumerate(labels_map.items()):
            lines += [f'    const-string v0, {quote(key)}',
                      '    invoke-virtual {v0, p0}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z',
                      '    move-result v0', f'    if-eqz v0, :ui_next_{i}',
                      f'    const-string v0, {quote(labels[0 if kind=="name" else 1])}',
                      '    return-object v0', f'    :ui_next_{i}']
    return '\n'.join(lines+['    :none','    const/4 v0, 0x0','    return-object v0','.end method'])

def make_init(profiles,debug=False):
    lines=['.method static constructor <clinit>()V','    .locals 2']
    if debug:
        lines += [f'    const-string v0, "hook loaded"',f'    invoke-static {{v0}}, {HOOK}->diag(Ljava/lang/String;)V']
    arrays=[]
    for i,p in enumerate(profiles):
        for strength in STRENGTHS:
            blend = blend_profile(p, strength)
            for kind,count,dex_type,values,width in [
                ('matrix',9,'[I',sum(blend['matrix'],[]),4),
                ('gamma',2048,'[B',[b for v in blend['gamma'] for b in (v&255,v>>8)],1)]:
                lines += [f'    const/16 v0, {hex(count)}',f'    new-array v1, v0, {dex_type}',
                          f'    fill-array-data v1, :data_{kind}_{i}_{strength}',
                          f'    sput-object v1, {HOOK}->sFuji{kind}{i}_{strength}:{dex_type}']
                arrays += [f'    :data_{kind}_{i}_{strength}',f'    .array-data {width}']
                arrays += ['        '+(('-0x%x'%-v if v<0 else '0x%x'%v)+('t' if width==1 else '')) for v in values]
                arrays += ['    .end array-data']
    return '\n'.join(lines+['    return-void']+arrays+['.end method'])

def preset_ids(profiles):
    lines=['.method public static getPresetIds()Ljava/util/List;','    .locals 2',
           '    new-instance v0, Ljava/util/ArrayList;',
           '    invoke-direct {v0}, Ljava/util/ArrayList;-><init>()V']
    for p in profiles:
        lines += [f'    const-string v1, {quote(p["id"])}',
                  '    invoke-virtual {v0, v1}, Ljava/util/ArrayList;->add(Ljava/lang/Object;)Z']
    return '\n'.join(lines+['    return-object v0','.end method'])

# The RGB matrix and the extended gamma table are the *hardware* look, and the
# body applies them inside the ISP. Some bodies report no support for the
# matrix, so applyHook leaves the matrix out and the camera's own Creative Style
# is the only look such a body can be given - applyNative reroutes each filter to
# its nearest style and picture effect. Whatever the fallback writes, it writes
# the *whole* look: a style on its own, or an effect left over from the previous
# filter, would add a colour that belongs to neither. The mapping is emitted inline
# instead of calling a lookup helper: a helper call that fails would be
# swallowed by this method's catch and the viewfinder would stay untouched,
# which is exactly the failure this feature has to rule out.
def native_hook(hook,profiles,debug=False):
    lines=[f'.method public static applyNative({MODIFIER_DESC}Ljava/lang/String;)V',
           '    .locals 3',
           '    :try_start',
           '    if-eqz p0, :native_end',
           '    if-eqz p1, :native_end']
    if debug:
        lines+=['    const-string v1, "native enter"',
                f'    invoke-static {{v1, p1}}, {hook}->diagStr(Ljava/lang/String;Ljava/lang/String;)V']
    for i,p in enumerate(profiles):
        lines+=[f'    const-string v1, {quote(p["id"])}',
                '    invoke-virtual {v1, p1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z',
                '    move-result v1',
                f'    if-eqz v1, :native_next_{i}']
        if debug:
            lines+=['    const-string v1, "native preset"',
                    f'    invoke-static {{v1, p1}}, {hook}->diagStr(Ljava/lang/String;Ljava/lang/String;)V']
        # Style first: a body that rejects the picture-effect token then still
        # shows the style, instead of showing nothing at all.
        lines+=[f'    const-string v0, {quote(p["native"]["mode"])}']
        if debug:
            lines+=['    const-string v1, "native style"',
                    f'    invoke-static {{v1, v0}}, {hook}->diagStr(Ljava/lang/String;Ljava/lang/String;)V']
        lines+=[f'    invoke-virtual {{p0, v0}}, {MODIFIER_DESC}->setColorMode(Ljava/lang/String;)V']
        effect=p['native']['picture_effect']
        # Every branch writes the effect, "off" included. A branch that wrote
        # only the style would leave the previous filter's effect on screen, and
        # that effect's own colour cast would then be read as part of the filter
        # that was just selected.
        lines+=[f'    const-string v0, {quote(effect or "off")}',
                f'    invoke-virtual {{p0, v0}}, {MODIFIER_DESC}->setPictureEffect(Ljava/lang/String;)V',
                # Record whether an effect of ours is on screen, so resetHook only
                # clears one that this hook actually set.
                f'    const/4 v1, {hex(1 if effect else 0)}',
                f'    sput-boolean v1, {hook}->sNativeEffectActive:Z']
        if debug:
            # Read both values back: a token this body rejects is dropped by the
            # setter, and that must not look like a working live preview.
            lines+=[f'    invoke-virtual {{p0}}, {MODIFIER_DESC}->getColorMode()Ljava/lang/String;',
                    '    move-result-object v1',
                    '    const-string v2, "native readback"',
                    f'    invoke-static {{v2, v1}}, {hook}->diagStr(Ljava/lang/String;Ljava/lang/String;)V']
        lines+=[f'    goto :native_end',
                f'    :native_next_{i}']
    lines+=['    :native_end',
            '    :try_end',
            '    .catch Ljava/lang/Throwable; {:try_start .. :try_end} :catch',
            '    return-void',
            '    :catch',
            '    move-exception v0']
    if debug:
        lines+=['    const-string v1, "native error"',
                '    invoke-virtual {v0}, Ljava/lang/Throwable;->toString()Ljava/lang/String;',
                '    move-result-object v2',
                f'    invoke-static {{v1, v2}}, {hook}->diagStr(Ljava/lang/String;Ljava/lang/String;)V']
    lines+=['    return-void',
            '.end method',
            f'.method public static needsNative({MODIFIER_DESC})Z',
            '    .locals 2',
            '    :try_start',
            '    if-eqz p0, :no_native',
            f'    invoke-virtual {{p0}}, {MODIFIER_DESC}->isRGBMatrixSupported()Z',
            '    move-result v0',
            # True only for the bodies whose ISP reports no RGB-matrix support:
            # those drop the matrix/gamma writes silently, so the camera's own
            # Creative Style is the only look that can reach the screen. A body
            # that reports support keeps the hardware look untouched.
            '    if-eqz v0, :no_native',
            '    const/4 v0, 0x1',
            '    return v0',
            '    :no_native',
            '    const/4 v0, 0x0',
            '    return v0',
            '    :try_end',
            '    .catch Ljava/lang/Throwable; {:try_start .. :try_end} :catch',
            '    :catch',
            '    move-exception v1',
            '    const/4 v0, 0x0',
            '    return v0',
            '.end method']
    return '\n'.join(lines)


def movie_hook():
    return f'''.method public static ensureForMovie()Z
    .locals 4
    :try_start
    invoke-static {{}}, {CTRL}->getInstance(){CTRL}
    move-result-object v0
    if-eqz v0, :failed
    invoke-virtual {{v0}}, {CTRL}->getBackupEffectValue()Ljava/lang/String;
    move-result-object v1
    const/4 v2, 0x0
    invoke-static {{v0, v2, v1}}, {HOOK}->applyHook({CTRL}Landroid/util/Pair;Ljava/lang/String;)Z
    move-result v2
    if-eqz v2, :failed
    const-string v0, "FujiHook"
    const-string v1, "Movie pre-start: selected filter applied; encoded output still needs verification"
    invoke-static {{v0, v1}}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I
    const/4 v0, 0x1
    return v0
    :try_end
    .catch Ljava/lang/Throwable; {{:try_start .. :try_end}} :catch
    :catch
    move-exception v0
    :failed
    const-string v0, "FujiHook"
    const-string v1, "Movie filter application failed; recording cancelled"
    invoke-static {{v0, v1}}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;)I
    const/4 v0, 0x0
    return v0
.end method'''

def movie_settings_log():
    controller='Lcom/sony/imaging/app/base/shooting/camera/MovieFormatController;'
    lines=['.method public static logMovieSettings()V', '    .locals 4', '    :try_start',
           f'    invoke-static {{}}, {controller}->getInstance(){controller}',
           '    move-result-object v0', '    new-instance v1, Ljava/lang/StringBuilder;',
           '    invoke-direct {v1}, Ljava/lang/StringBuilder;-><init>()V']
    for tag in ('movie_format_menu', 'record_setting'):
        lines += [f'    const-string v2, "{tag}="',
                  '    invoke-virtual {v1, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;',
                  f'    const-string v2, "{tag}"',
                  f'    invoke-virtual {{v0, v2}}, {controller}->getValue(Ljava/lang/String;)Ljava/lang/String;',
                  '    move-result-object v2',
                  '    invoke-virtual {v1, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;',
                  '    const-string v2, "; "',
                  '    invoke-virtual {v1, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;']
    lines += ['    const-string v2, "strength="',
              '    invoke-virtual {v1, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;',
              f'    invoke-static {{}}, {HOOK}->getStrengthValue()Ljava/lang/String;',
              '    move-result-object v2',
              '    invoke-virtual {v1, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;',
              '    const-string v2, "FujiMovieSettings"',
              '    invoke-virtual {v1}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;',
              '    move-result-object v3',
              '    invoke-static {v2, v3}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I',
              '    :try_end', '    .catch Ljava/lang/Throwable; {:try_start .. :try_end} :catch',
              '    return-void', '    :catch', '    move-exception v0', '    return-void', '.end method']
    return '\n'.join(lines)

# The camera HAL decides whether a custom look can reach the ISP at all, so the
# live-view fallback asks it instead of keeping a model list. The answer is
# only used to choose a look: a body that throws from the query must keep the
# hardware behaviour, which is why needsNative() answers false on any error.
MODIFIER_DESC='Lcom/sony/scalar/hardware/CameraEx$ParametersModifier;'
# Single source of truth for the one instruction both the diagnostics and the
# native-preview variant hook into: the point in applyHook where the modifier
# is fully populated but has not been committed to the HAL yet.
MATRIX_CALL='    invoke-virtual {v2, v3}, Lcom/sony/scalar/hardware/CameraEx$ParametersModifier;->setRGBMatrix([I)V\n'
# The extended gamma table is the second optional half of the look. It is read
# from the same Pair after the matrix was committed.
GAMMA_CALL=('    invoke-static {p0}, Lcom/sony/imaging/app/pictureeffectplus/shooting/camera/RicohHook;'
            '->getCameraEx(Lcom/sony/imaging/app/pictureeffectplus/shooting/camera/PictureEffectPlusController;)'
            'Lcom/sony/scalar/hardware/CameraEx;\n'
            '    move-result-object v2\n')


def build_diag_str():
    """Log '<label>=<value>'; used to read a value back after writing it."""
    return '\n'.join([
        '.method public static diagStr(Ljava/lang/String;Ljava/lang/String;)V',
        '    .locals 3',
        '    :try_start',
        '    new-instance v0, Ljava/lang/StringBuilder;',
        '    invoke-direct {v0}, Ljava/lang/StringBuilder;-><init>()V',
        '    invoke-virtual {v0, p0}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;',
        '    move-result-object v0',
        '    const-string v1, "="',
        '    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;',
        '    move-result-object v0',
        '    invoke-virtual {v0, p1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;',
        '    move-result-object v0',
        '    invoke-virtual {v0}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;',
        '    move-result-object v0',
        '    const-string v1, "FujiDiag"',
        '    invoke-static {v1, v0}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I',
        f'    invoke-static {{v0}}, {HOOK}->diagFile(Ljava/lang/String;)V',
        '    :try_end',
        '    .catch Ljava/lang/Throwable; {:try_start .. :try_end} :catch',
        '    return-void',
        '    :catch',
        '    move-exception v0',
        '    return-void',
        '.end method',
        ''])


# Diagnostic-only helpers for --debug builds. They only log; the filter and
# icon behaviour is unchanged, so a debug build tells us where a device fails
# without altering what it does.
DIAG_METHODS = '''
.method public static diag(Ljava/lang/String;)V
    .locals 3
    :try_start
    new-instance v0, Ljava/lang/StringBuilder;
    invoke-direct {v0}, Ljava/lang/StringBuilder;-><init>()V
    const-string v1, "diag "
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    invoke-virtual {v0, p0}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    const-string v1, " sdk="
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    sget v1, Landroid/os/Build$VERSION;->SDK_INT:I
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(I)Ljava/lang/StringBuilder;
    move-result-object v0
    const-string v1, " model="
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    sget-object v1, Landroid/os/Build;->MODEL:Ljava/lang/String;
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    invoke-virtual {v0}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;
    move-result-object v0
    const-string v1, "FujiDiag"
    invoke-static {v1, v0}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I
    invoke-static {v0}, Lcom/sony/imaging/app/pictureeffectplus/shooting/camera/RicohHook;->diagFile(Ljava/lang/String;)V
    :try_end
    .catch Ljava/lang/Throwable; {:try_start .. :try_end} :catch
    return-void
    :catch
    move-exception v0
    return-void
.end method

.method public static diagIcon(Ljava/lang/String;I)V
    .locals 3
    :try_start
    new-instance v0, Ljava/lang/StringBuilder;
    invoke-direct {v0}, Ljava/lang/StringBuilder;-><init>()V
    const-string v1, "icon "
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    invoke-virtual {v0, p0}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    const-string v1, " -> "
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    invoke-static {p1}, Ljava/lang/Integer;->toString(I)Ljava/lang/String;
    move-result-object v1
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    invoke-virtual {v0}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;
    move-result-object v0
    const-string v1, "FujiDiag"
    invoke-static {v1, v0}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I
    invoke-static {v0}, Lcom/sony/imaging/app/pictureeffectplus/shooting/camera/RicohHook;->diagFile(Ljava/lang/String;)V
    :try_end
    .catch Ljava/lang/Throwable; {:try_start .. :try_end} :catch
    return-void
    :catch
    move-exception v0
    return-void
.end method

.method public static diagCapabilities(Lcom/sony/scalar/hardware/CameraEx$ParametersModifier;Ljava/lang/String;)V
    .locals 4
    :try_start
    new-instance v0, Ljava/lang/StringBuilder;
    invoke-direct {v0}, Ljava/lang/StringBuilder;-><init>()V
    const-string v1, "caps "
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    invoke-virtual {v0, p1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    const-string v1, " matrix="
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    const/4 v2, 0x0
    if-eqz p0, :no_modifier
    invoke-virtual {p0}, Lcom/sony/scalar/hardware/CameraEx$ParametersModifier;->isRGBMatrixSupported()Z
    move-result v2
    :no_modifier
    invoke-virtual {v0, v2}, Ljava/lang/StringBuilder;->append(Z)Ljava/lang/StringBuilder;
    move-result-object v0
    const-string v1, " gamma="
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    const/4 v2, 0x0
    if-eqz p0, :no_modifier2
    invoke-virtual {p0}, Lcom/sony/scalar/hardware/CameraEx$ParametersModifier;->isExtendedGammaTableSupported()Z
    move-result v2
    :no_modifier2
    invoke-virtual {v0, v2}, Ljava/lang/StringBuilder;->append(Z)Ljava/lang/StringBuilder;
    move-result-object v0
    invoke-virtual {v0}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;
    move-result-object v0
    const-string v1, "FujiDiag"
    invoke-static {v1, v0}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I
    invoke-static {v0}, Lcom/sony/imaging/app/pictureeffectplus/shooting/camera/RicohHook;->diagFile(Ljava/lang/String;)V
    :try_end
    .catch Ljava/lang/Throwable; {:try_start .. :try_end} :catch
    return-void
    :catch
    move-exception v0
    return-void
.end method

.method public static diagObj(Ljava/lang/Object;Ljava/lang/String;)V
    .locals 3
    :try_start
    new-instance v0, Ljava/lang/StringBuilder;
    invoke-direct {v0}, Ljava/lang/StringBuilder;-><init>()V
    invoke-virtual {v0, p1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    const-string v1, " null="
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    const/4 v1, 0x0
    if-nez p0, :not_null
    const/4 v1, 0x1
    :not_null
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Z)Ljava/lang/StringBuilder;
    move-result-object v0
    invoke-virtual {v0}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;
    move-result-object v0
    const-string v1, "FujiDiag"
    invoke-static {v1, v0}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I
    invoke-static {v0}, Lcom/sony/imaging/app/pictureeffectplus/shooting/camera/RicohHook;->diagFile(Ljava/lang/String;)V
    :try_end
    .catch Ljava/lang/Throwable; {:try_start .. :try_end} :catch
    return-void
    :catch
    move-exception v0
    return-void
.end method

.method public static diagInt(ILjava/lang/String;)V
    .locals 3
    :try_start
    new-instance v0, Ljava/lang/StringBuilder;
    invoke-direct {v0}, Ljava/lang/StringBuilder;-><init>()V
    invoke-virtual {v0, p1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    const-string v1, "="
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    invoke-static {p0}, Ljava/lang/Integer;->toString(I)Ljava/lang/String;
    move-result-object v1
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    invoke-virtual {v0}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;
    move-result-object v0
    const-string v1, "FujiDiag"
    invoke-static {v1, v0}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I
    invoke-static {v0}, Lcom/sony/imaging/app/pictureeffectplus/shooting/camera/RicohHook;->diagFile(Ljava/lang/String;)V
    :try_end
    .catch Ljava/lang/Throwable; {:try_start .. :try_end} :catch
    return-void
    :catch
    move-exception v0
    return-void
.end method

# Append to the camera's storage card so no adb is needed to collect the log;
# failures (no card, read-only path) are swallowed on purpose.
.method public static diagFile(Ljava/lang/String;)V
    .locals 3
    :try_start
    new-instance v0, Ljava/io/FileWriter;
    const-string v1, "/mnt/sdcard/FilmStudioDiag.txt"
    const/4 v2, 0x1
    invoke-direct {v0, v1, v2}, Ljava/io/FileWriter;-><init>(Ljava/lang/String;Z)V
    invoke-virtual {v0, p0}, Ljava/io/FileWriter;->write(Ljava/lang/String;)V
    const/16 v2, 0xa
    invoke-virtual {v0, v2}, Ljava/io/FileWriter;->write(I)V
    invoke-virtual {v0}, Ljava/io/FileWriter;->close()V
    :try_end
    .catch Ljava/lang/Throwable; {:try_start .. :try_end} :catch
    return-void
    :catch
    move-exception v0
    return-void
.end method
'''


def annotate_apply(apply,hook,debug):
    """Log every silent failure path in applyHook so one device log is enough."""
    if not debug:
        return apply
    assert apply.count('    .locals 6') == 1
    apply=apply.replace('    .locals 6','    .locals 8',1)
    for pattern,reason in (
        (r'    if-nez v3, :cond_0\n(?:[ \t]*\n)*    return v0\n','applyHook: matrix=null'),
        (r'    if-nez v1, :cond_1\n(?:[ \t]*\n)*    return v0\n','applyHook: cameraSetting=null'),
        (r'    if-nez p1, :cond_3\n(?:[ \t]*\n)*    return v0\n','applyHook: params=null')):
        assert len(re.findall(pattern,apply)) == 1, pattern
        apply=re.sub(pattern,lambda m,r=reason: m.group(0).replace('    return v0\n',
            f'    const-string v6, "{r}"\n\n    invoke-static {{v6}}, {hook}->diag(Ljava/lang/String;)V\n\n    return v0\n'),
            apply,count=1)
    cast='    check-cast v2, Lcom/sony/scalar/hardware/CameraEx$ParametersModifier;\n'
    assert apply.count(cast) == 1
    matrix_call=MATRIX_CALL
    assert apply.count(matrix_call) == 1
    # v2 is the modifier and is known non-null inside this branch, so this is
    # the only place where the capability probe is meaningful.
    apply=apply.replace(matrix_call,('    const-string v5, "before-matrix"\n\n'
        f'    invoke-static {{v2, v5}}, {hook}->diagCapabilities(Lcom/sony/scalar/hardware/CameraEx$ParametersModifier;Ljava/lang/String;)V\n\n'
        # The strength chooses which compiled arrays are used, so log the value
        # the hook actually reads: a fresh install has to default to 50.
        '    const-string v5, "strength"\n\n'
        f'    invoke-static {{}}, {hook}->getStrengthValue()Ljava/lang/String;\n\n'
        '    move-result-object v6\n\n'
        f'    invoke-static {{v5, v6}}, {hook}->diagStr(Ljava/lang/String;Ljava/lang/String;)V\n\n'
        )+matrix_call,1)
    camera_ex='    invoke-static {p0}, Lcom/sony/imaging/app/pictureeffectplus/shooting/camera/RicohHook;->getCameraEx(Lcom/sony/imaging/app/pictureeffectplus/shooting/camera/PictureEffectPlusController;)Lcom/sony/scalar/hardware/CameraEx;\n    move-result-object v2\n'
    assert apply.count(camera_ex) == 1
    apply=apply.replace(camera_ex,camera_ex+f'    const-string v5, "cameraEx"\n\n    invoke-static {{v2, v5}}, {hook}->diagObj(Ljava/lang/Object;Ljava/lang/String;)V\n\n',1)
    assert camera_ex == GAMMA_CALL, 'gamma anchor drifted from the upstream hook'
    gamma_table='    invoke-virtual {v2}, Lcom/sony/scalar/hardware/CameraEx;->createGammaTable()Lcom/sony/scalar/hardware/CameraEx$GammaTable;\n    move-result-object v3\n'
    assert apply.count(gamma_table) == 1
    apply=apply.replace(gamma_table,gamma_table+f'    const-string v5, "gammaTable"\n\n    invoke-static {{v3, v5}}, {hook}->diagObj(Ljava/lang/Object;Ljava/lang/String;)V\n\n',1)
    gamma_write='    invoke-virtual {v3, v0}, Lcom/sony/scalar/hardware/CameraEx$GammaTable;->write(Ljava/io/InputStream;)I\n'
    assert apply.count(gamma_write) == 1
    apply=apply.replace(gamma_write,gamma_write+f'    move-result v4\n\n    const-string v5, "gammaWrite"\n\n    invoke-static {{v4, v5}}, {hook}->diagInt(ILjava/lang/String;)V\n\n',1)
    gamma_commit='    invoke-virtual {v2, v3}, Lcom/sony/scalar/hardware/CameraEx;->setExtendedGammaTable(Lcom/sony/scalar/hardware/CameraEx$GammaTable;)V\n'
    assert apply.count(gamma_commit) == 1
    apply=apply.replace(gamma_commit,gamma_commit+f'    const-string v5, "gammaCommit"\n\n    invoke-static {{v5}}, {hook}->diag(Ljava/lang/String;)V\n\n',1)
    success='    :cond_5\n    const/4 v2, 0x1\n'
    assert apply.count(success) == 1
    apply=apply.replace(success,f'    :cond_5\n    invoke-static {{p2}}, {hook}->diag(Ljava/lang/String;)V\n\n    const/4 v2, 0x1\n',1)
    return apply


def annotate_icons(layout,hook,debug):
    """Log the resolved thumbnail IDs of the filter chooser."""
    text=layout.read_text(encoding='utf-8')
    if not debug:
        return
    needle='    invoke-virtual {v1}, Ljava/lang/Integer;->intValue()I\n\n    move-result v0\n'
    assert text.count(needle) == 1, 'getBackgroundDrawable lookup not found'
    text=text.replace(needle,needle+f'\n    invoke-static {{p1, v0}}, {hook}->diagIcon(Ljava/lang/String;I)V\n',1)
    layout.write_text(text,encoding='utf-8')


def patch_hook(path,profiles,upstream_hook,movie=False,debug=False,native_preview=False,
               gate_gamma=False):
    text=path.read_text(encoding='utf-8')
    fields='\n'.join(f'.field private static sFuji{k}{i}_{strength}:{t}' for i in range(len(profiles))
                     for strength in STRENGTHS
                     for k,t in [('matrix','[I'),('gamma','[B')])
    # Two flags report what this hook currently has on the camera, so resetHook
    # can hand back exactly what applyHook took. Nothing else may rely on them.
    fields+=('\n.field private static sNativeEffectActive:Z'
             '\n.field private static sExtendedGammaActive:Z')
    text=text.replace('# static fields','# static fields\n'+fields)
    text=replace_method(text,'<clinit>()V',make_init(profiles,debug))
    for method,kind in [('getGammaBytes','gamma'),('getRGBMatrix','matrix'),
                        ('getFilterName','name'),('getFilterGuide','guide')]:
        ret={'gamma':'[B','matrix':'[I','name':'Ljava/lang/String;',
             'guide':'Ljava/lang/String;'}[kind]
        text=replace_method(text,method+'(Ljava/lang/String;)'+ret,lookup_method(method,profiles,kind,movie))
    original=upstream_hook.read_text(encoding='utf-8')
    apply=re.search(r'^\.method public static applyHook\([\s\S]*?^\.end method',original,re.M).group()
    # Keep upstream's graceful degradation. A missing ParametersModifier,
    # CameraEx or GammaTable must only skip the optional step; it must never
    # cancel the RGB matrix that drives the live preview. The caller ignores
    # this return value, so "fail closed" only drops the effect silently and
    # also makes every strength change roll back on models without the DMA
    # gamma path.
    apply=annotate_apply(apply,HOOK,debug)
    assert apply.count(MATRIX_CALL) == 1, 'native anchor'
    # A body that reports no RGB-matrix support is the body that silently drops
    # the writes, so ask it instead of keeping a model list. --native-preview
    # forces the style for a body that reports support but still shows nothing.
    # needsNative() answers false if the query itself fails, so a body with an
    # unexpected HAL keeps the hardware look instead of silently changing.
    gate=('' if native_preview else
          f'    invoke-static {{v2}}, {HOOK}->needsNative({MODIFIER_DESC})Z\n'
          '    move-result v4\n'
          '    if-eqz v4, :skip_native\n\n')
    release='' if native_preview else '\n    :skip_native\n'
    mark=(f'    const-string v5, "after-native"\n\n'
          f'    invoke-static {{v5}}, {HOOK}->diag(Ljava/lang/String;)V\n\n') if debug else ''
    apply_native=(f'    invoke-static {{v2, p2}}, {HOOK}->applyNative({MODIFIER_DESC}'
                  'Ljava/lang/String;)V\n')
    # The matrix is written on every body. A body that reports no RGB-matrix
    # support still applies it - measured on an a7R II, where the fitted matrices
    # changed the stills while the same body ignored every Creative Style and
    # Picture Effect write - so applyNative is advisory only and must never
    # suppress the matrix that carries the look. Keep it, but write the matrix
    # in the same commit either way.
    apply=apply.replace(MATRIX_CALL, gate+apply_native+release+mark+MATRIX_CALL,1)
    # The extended gamma table is written together with the matrix, without
    # asking the body first, exactly as upstream does. The matrix is only the
    # look together with the curve it was fitted against, and the capability
    # queries cannot be trusted to decide either half: an a7R II answers false
    # to both and still applies the matrix. Gating the table on that answer
    # leaves a matrix writing on its own, which does not render the look and
    # instead shifts hue on saturated colour (a cyan sky, a green cast on a
    # sunlit wall) - measured on an a7R II. --gate-extended-gamma restores the
    # probe for comparison.
    if gate_gamma:
        assert apply.count(GAMMA_CALL) == 1, 'gamma anchor'
        probe=('    const/4 v5, 0x0\n'
               '    :try_start_gamma_probe\n'
               '    iget-object v4, p1, Landroid/util/Pair;->second:Ljava/lang/Object;\n'
               f'    check-cast v4, {MODIFIER_DESC}\n'
               '    if-eqz v4, :gamma_probe_done\n'
               f'    invoke-virtual {{v4}}, {MODIFIER_DESC}->isExtendedGammaTableSupported()Z\n'
               '    move-result v5\n'
               '    :gamma_probe_done\n'
               '    :try_end_gamma_probe\n'
               '    .catch Ljava/lang/Throwable; {:try_start_gamma_probe .. :try_end_gamma_probe}'
               ' :gamma_probe_error\n'
               '    goto :gamma_probe_checked\n'
               '    :gamma_probe_error\n'
               '    move-exception v4\n'
               '    const/4 v5, 0x1\n'
               '    :gamma_probe_checked\n'
               '    if-eqz v5, :cond_5\n')
        apply=apply.replace(GAMMA_CALL,probe+GAMMA_CALL,1)
        gamma_commit=('    invoke-virtual {v2, v3}, Lcom/sony/scalar/hardware/CameraEx;'
                      '->setExtendedGammaTable(Lcom/sony/scalar/hardware/CameraEx$GammaTable;)V\n')
        assert apply.count(gamma_commit) == 1, 'gamma commit anchor'
        apply=apply.replace(gamma_commit,gamma_commit
            +f'    const/4 v4, 0x1\n    sput-boolean v4, {HOOK}->sExtendedGammaActive:Z\n',1)
    text=replace_method(text,'applyHook('+CTRL+'Landroid/util/Pair;Ljava/lang/String;)Z',apply)
    if gate_gamma:
        # Apktool separates the decoded instructions with blank lines, so this
        # three-line upstream pattern has to be located line by line instead of
        # as one literal string.
        gamma_clear=('    invoke-virtual {v0, v1}, Lcom/sony/scalar/hardware/CameraEx;'
                     '->setExtendedGammaTable(Lcom/sony/scalar/hardware/CameraEx$GammaTable;)V')
        lines=text.splitlines(keepends=True)
        significant=[i for i,line in enumerate(lines) if line.strip()]
        hits=[i for i in significant if lines[i].rstrip('\n')==gamma_clear]
        assert len(hits)==1, 'resetHook gamma anchor'
        position=significant.index(hits[0])
        assert position >= 2 and [lines[significant[position-2]].strip(),
                                  lines[significant[position-1]].strip()] == \
            ['if-eqz v0, :cond_1', 'const/4 v1, 0x0'], 'resetHook gamma anchor shape'
        guard=significant[position-2]
        lines[guard:guard]=[
            f'    sget-boolean v1, {HOOK}->sExtendedGammaActive:Z\n',
            '    const/4 v2, 0x0\n',
            f'    sput-boolean v2, {HOOK}->sExtendedGammaActive:Z\n',
            '    if-eqz v1, :cond_1\n',
            '\n',
        ]
        text=''.join(lines)
    # The live-view fallback sets one of the camera's own Picture Effects, so
    # resetHook has to take it back off - but only when the fallback really ran,
    # so a body that uses the hardware look keeps upstream's reset byte for byte.
    reset_anchor=('    invoke-virtual {v1, v2}, Lcom/sony/scalar/hardware/CameraEx$ParametersModifier;'
                  '->setColorMode(Ljava/lang/String;)V\n')
    assert text.count(reset_anchor) == 1, 'resetHook anchor'
    reset_method=re.search(r'^\.method public static resetHook\([\s\S]*?^\.end method',text,re.M).group()
    # The anchor reads v2, which already holds the neutral Creative Style token,
    # so the effect reset has to use resetHook's otherwise unused v3: writing
    # "off" into v2 would hand that invalid style to setColorMode().
    assert '    .locals 4\n' in reset_method, 'resetHook must keep a free v3 for the effect reset'
    assert ':no_native_effect_reset' not in text
    text=text.replace(reset_anchor,
                      f'    sget-boolean v3, {HOOK}->sNativeEffectActive:Z\n'
                      '    if-eqz v3, :no_native_effect_reset\n'
                      '    const/4 v3, 0x0\n'
                      f'    sput-boolean v3, {HOOK}->sNativeEffectActive:Z\n'
                      '    const-string v3, "off"\n'
                      '    invoke-virtual {v1, v3}, Lcom/sony/scalar/hardware/CameraEx$ParametersModifier;'
                      '->setPictureEffect(Ljava/lang/String;)V\n'
                      '    :no_native_effect_reset\n'+reset_anchor,1)
    text+='\n'+preset_ids(profiles)+'\n'+movie_hook()+'\n'+strength_methods(HOOK,CTRL)+'\n'
    text+='\n'+native_hook(HOOK,profiles,debug)+'\n'
    text+='\n'+QUALITY_FILTER_METHOD+'\n'
    if movie and debug:
        # Pure diagnostics: keep them out of release builds, where they would
        # re-enter MovieFormatController from inside its own setValue().
        text+='\n'+movie_settings_log()+'\n'
    if debug:
        text+=DIAG_METHODS+build_diag_str()
    text=text.replace('"RicohHook"','"FujiHook"').replace('Ricoh preset','Fuji approximation')
    path.write_text(text,encoding='utf-8')

# A thumbnail is a numeric resource ID, so it is only meaningful inside the
# exact base APK it was read from. Never hardcode one: reuse the IDs the base
# APK already uses for its own picture effects, and keep its existing entries
# instead of wiping the whole map (other menus read the same map).
RICOH_ICON_SOURCE = {
    'ricoh-positive': 'pop-color',
    'ricoh-negative': 'retro-photo',
    'ricoh-hcbw': 'richtone-mono',
    'ricoh-daido': 'rough-mono',
    'ricoh-cross': 'watercolor',
}
ICON_SOURCE_ORDER = ('pop-color', 'retro-photo', 'richtone-mono', 'rough-mono', 'watercolor')
# Leica looks take the thumbnails that read closest to them. Nothing else is
# available: a thumbnail is a resource ID of the base APK's own picture effects.
LEICA_ICON_SOURCE = {
    'leica-classic': 'retro-photo',
    'leica-natural': 'watercolor',
}


def read_icon_map(text):
    """item ID -> thumbnail resource ID, from an initializeIconMap() body."""
    icons, item, distance = {}, None, 0
    for line in text.splitlines():
        if item is not None:
            value = re.match(r'^\s*const(?:/16)? v\d+, (0x[0-9a-fA-F]+)\s*$', line)
            if value:
                icons[item], item = int(value.group(1), 16), None
            elif line.strip():
                distance += 1
                if distance > 3:
                    item = None
            continue
        name = re.match(r'^\s*const-string v\d+, "([^"]+)"\s*$', line)
        if name:
            item, distance = name.group(1), 0
    return icons


def base_icon_map(layout_text):
    """Reads the base APK's own map so its thumbnails can be reused as-is."""
    found = re.search(r'^\.method private initializeIconMap\(\)V\n[\s\S]*?^\.end method',
                      layout_text, re.M)
    if not found:
        raise SystemExit('Base APK has no initializeIconMap()V: its thumbnails cannot be reused')
    icons = read_icon_map(found.group())
    if not icons:
        raise SystemExit('Could not read any thumbnail ID from the base APK icon map')
    return icons


def icon_source(profile):
    """Which base-APK effect supplies this profile's thumbnail."""
    if profile.get('family') == 'ricoh':
        return RICOH_ICON_SOURCE.get(profile['id'])
    if profile.get('family') == 'leica':
        return LEICA_ICON_SOURCE.get(profile['id'])
    return 'richtone-mono' if 'ACROS' in profile.get('official_film', '').upper() else 'pop-color'


def icon_map_method(cls, layout_text, profiles):
    existing = base_icon_map(layout_text)
    fallbacks = [name for name in ICON_SOURCE_ORDER if name in existing] or sorted(existing)
    entries = list(existing.items())
    for p in profiles:
        wanted = icon_source(p)
        if wanted not in existing:
            print(f'Warning: base APK has no {wanted!r} thumbnail; {p["id"]} uses {fallbacks[0]!r}')
        entries.append((p['id'], existing.get(wanted, existing[fallbacks[0]])))
    lines = ['.method private initializeIconMap()V', '    .locals 3',
             '    new-instance v0, Ljava/util/HashMap;',
             '    invoke-direct {v0}, Ljava/util/HashMap;-><init>()V',
             f'    sput-object v0, {cls}->mItemIconMap:Ljava/util/HashMap;']
    for item, icon in entries:
        lines += [f'    const-string v1, {quote(item)}',
                  f'    const v2, {hex(icon)}',
                  '    invoke-static {v2}, Ljava/lang/Integer;->valueOf(I)Ljava/lang/Integer;',
                  '    move-result-object v2',
                  '    invoke-virtual {v0, v1, v2}, Ljava/util/HashMap;->put(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;']
    return '\n'.join(lines+['    return-void','.end method'])


def patch_option_menu_live(layout,scrim_alpha):
    """Keep the live preview visible while the filter is being chosen.

    The activity runs with Theme.Transparent, so the camera preview surface sits
    behind the window. The menu then hides it twice: the layout root paints
    @android:color/black over the whole screen, and the chooser paints the base
    APK's static per-effect illustration on top of that. Clearing both leaves the
    preview visible, which is what a live filter chooser needs. 0 is the value
    the app's own releaseImageViewDrawable() already passes to clear a
    background.

    The root is cleared, and the list panel is left transparent by default: the
    activity has no touch screen and the live view is the whole point of this
    chooser. In pictureeffectplus_menu_scn_option.xml the preview column is the
    280x480 area on the right (where sceneselection_icon used to be), but the
    name and guide text of the chooser (484px wide) are laid across it, so the
    only region that could be darkened without touching the preview is the
    122px icon list. --menu-scrim paints the list panel for a reader who prefers
    darker text backgrounds and accepts the preview being dimmed with it.
    """
    text=layout.read_text(encoding='utf-8')
    needle=('    invoke-direct {p0, v1}, Lcom/sony/imaging/app/pictureeffectplus/shooting/layout/'
            'PictureEffectPlusOptionMenuLayout;->getBackgroundDrawable(Ljava/lang/String;)I\n'
            '\n'
            '    move-result v1\n')
    assert text.count(needle) == 2, text.count(needle)
    text=text.replace(needle,needle+'    const/4 v1, 0x0\n\n')
    root=('    iget-object v1, p0, Lcom/sony/imaging/app/pictureeffectplus/shooting/layout/'
          'PictureEffectPlusOptionMenuLayout;->mCurrentView:Landroid/view/ViewGroup;\n'
          '\n'
          '    return-object v1\n')
    assert text.count(root) == 1, 'onCreateView root view anchor'
    text=text.replace(root,
        '    iget-object v1, p0, Lcom/sony/imaging/app/pictureeffectplus/shooting/layout/'
        'PictureEffectPlusOptionMenuLayout;->mViewArea:Lcom/sony/imaging/app/base/menu/layout/'
        'SpecialScreenArea;\n'
        '\n'
        f'    const/high16 v0, 0x{scrim_alpha:02X}000000\n'
        '\n'
        '    invoke-virtual {v1, v0}, Landroid/view/View;->setBackgroundColor(I)V\n'
        '\n'
        '    iget-object v1, p0, Lcom/sony/imaging/app/pictureeffectplus/shooting/layout/'
        'PictureEffectPlusOptionMenuLayout;->mSpecialScreenView:Lcom/sony/imaging/app/base/menu/'
        'layout/SpecialScreenView;\n'
        '\n'
        f'    const/high16 v0, 0x{scrim_alpha:02X}000000\n'
        '\n'
        '    invoke-virtual {v1, v0}, Landroid/view/View;->setBackgroundColor(I)V\n'
        '\n'
        '    iget-object v1, p0, Lcom/sony/imaging/app/pictureeffectplus/shooting/layout/'
        'PictureEffectPlusOptionMenuLayout;->mCurrentView:Landroid/view/ViewGroup;\n'
        '\n'
        '    const/4 v0, 0x0\n'
        '\n'
        '    invoke-virtual {v1, v0}, Landroid/view/View;->setBackgroundColor(I)V\n'
        '\n'
        '    return-object v1\n',1)
    layout.write_text(text,encoding='utf-8')


def fix_menu_key_exit(layout,cls):
    """Let MENU leave the chooser even when the center key opened it.

    pushedMenuKey() has two exits. With a menu history it defers to the base
    implementation, which pops the history and, through closeMenuLayout(),
    tells the menu state that the layout closed. Without one - the chooser
    opened by the center key from the shooting screen, where
    getLastStoredValues() returns early because the bundle carries no menu
    history and leaves mLastItemId null - it called closeLayout() directly.

    closeLayout() only tears the layout down after committing the effect: the
    menu state stayed on the stack with no layout behind it, so the app stopped
    reacting to every key while the rest of the system stayed healthy, and the
    camera needed a power cycle. Reaching the same teardown through
    openPreviousMenu() runs closeLayout() once, exactly as before, and then
    onClosed(), which is the notification the state machine waits for.
    """
    text=layout.read_text(encoding='utf-8')
    method=re.search(r'^\.method public pushedMenuKey\(\)I\n[\s\S]*?^\.end method',text,re.M).group()
    call=f'    invoke-virtual {{p0}}, {cls}->closeLayout()V\n'
    assert method.count(call) == 1, 'pushedMenuKey closeLayout anchor'
    fixed=('    invoke-virtual {p0}, Lcom/sony/imaging/app/base/menu/layout/'
           'BaseMenuLayout;->openPreviousMenu()V\n')
    layout.write_text(text.replace(method,method.replace(call,fixed)),encoding='utf-8')


# RAW capture entries added under the still-image quality menu. ItemId/Value
# follow the upstream Ricoh v1.6.0 scheme: the menu carries ItemId
# setPictureStorageFormat_<fmt> while the controller receives the short Value.
# Titles are written here as the static fallback; getFilterName/getFilterGuide
# resolve the localized label for the same ItemId through lookup_method().
RAW_MENU_ENTRIES = (
    dict(item_id='setPictureStorageFormat_rawjpeg', value='rawjpeg',
         title='RAW与JPEG',
         caution='CAUTION_GRP_ID_STILL_IMAGE_QUALITY_RAW_JPEG_INVALID_GUIDE',
         icon='drawable/p_16_dd_parts_5w_shoot_icon_imgquality_uncompressed_raw_j'),
    dict(item_id='setPictureStorageFormat_raw', value='raw',
         title='RAW',
         caution='CAUTION_GRP_ID_STILL_IMAGE_QUALITY_RAW_INVALID_GUIDE',
         icon='drawable/p_16_dd_parts_5w_shoot_icon_imgquality_uncompressed_raw'),
)
QUALITY_MENU_ID = 'setPictureStorageFormat'

# Display labels for the two RAW entries, keyed by their ItemId - the same key
# the menu layer hands to getFilterName()/getFilterGuide() for every other item.
RAW_LABELS = {
    'setPictureStorageFormat_rawjpeg': ('RAW与JPEG', '同时记录RAW图像和JPEG图像。'),
    'setPictureStorageFormat_raw': ('RAW', '记录RAW图像。'),
}

# Upstream v1.6.0 RicohHook.filterQualityAvailability: force the two RAW quality
# values to "available" whatever the body's own answer was, pass others through.
QUALITY_FILTER_METHOD = f'''.method public static filterQualityAvailability(Ljava/lang/String;Z)Z
    .locals 1
    if-nez p1, :cond_orig_ok
    const-string v0, "rawjpeg"
    invoke-virtual {{v0, p0}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-eqz v0, :cond_check_raw
    const/4 v0, 0x1
    return v0
    :cond_check_raw
    const-string v0, "raw"
    invoke-virtual {{v0, p0}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-eqz v0, :cond_orig_ok
    const/4 v0, 0x1
    return v0
    :cond_orig_ok
    return p1
.end method'''


def patch_quality_menu(base):
    """Add RAW+JPEG and RAW to the still-image quality menu (upstream v1.6.0)."""
    path = base/'assets/MenuData.xml'
    tree = ET.parse(path)
    layer1 = next((x for x in tree.iter()
                   if x.tag == 'Layer1' and x.get('ItemId') == QUALITY_MENU_ID), None)
    if layer1 is None:
        raise ValueError('MenuData.xml has no Layer1 with ItemId=' + QUALITY_MENU_ID)
    existing = {x.get('ItemId') for x in layer1}
    added = []
    for spec in RAW_MENU_ENTRIES:
        if spec['item_id'] in existing:
            continue
        item = ET.SubElement(layer1, 'Layer2')
        item.attrib.update(
            CautionID=spec['caution'],
            ConfigClass='com.sony.imaging.app.base.shooting.camera.PictureQualityController',
            DisplayName=spec['title'], ExecType='SET_VALUE', GuideRes='',
            IconRes=spec['icon'], ItemId=spec['item_id'],
            SelectedIconRes=spec['icon'], TextRes='',
            Title=spec['title'], Value=spec['value'])
        added.append(spec['item_id'])
    if added:
        ET.indent(tree, space='    ')
        tree.write(path, encoding='utf-8', xml_declaration=True)
    return added


def patch_quality_controller(base):
    """Route PictureQualityController's availability answer through the hook.

    The base app marks rawjpeg/raw unavailable for this generation; the hook
    forces them on. Anchors on the single AvailableInfo.isAvailable call inside
    getAvailableValue, matching upstream v1.6.0's patch_picture_quality_smali.
    """
    path = (base/'smali/com/sony/imaging/app/base/shooting/camera/'
            'PictureQualityController.smali')
    if not path.exists():
        raise FileNotFoundError('PictureQualityController.smali not in base APK')
    text = path.read_text(encoding='utf-8')
    if 'filterQualityAvailability' in text:
        return False  # already patched
    anchor = ('    invoke-static {v6}, Lcom/sony/imaging/app/util/AvailableInfo;'
              '->isAvailable([Ljava/lang/Object;)Z\n\n    move-result v6')
    if text.count(anchor) != 1:
        raise ValueError('PictureQualityController isAvailable anchor not unique/found')
    injection = (anchor +
        '\n\n    invoke-static {v3, v6}, ' + HOOK +
        '->filterQualityAvailability(Ljava/lang/String;Z)Z\n\n    move-result v6')
    path.write_text(text.replace(anchor, injection, 1), encoding='utf-8')
    return True


def patch_menu(base,profiles,debug=False,live=True,scrim_alpha=DEFAULT_MENU_SCRIM):
    path=base/'assets/MenuData.xml'
    tree=ET.parse(path)
    parent=next(x for x in tree.iter() if x.get('ItemId')=='ApplicationTop')
    template=copy.deepcopy(parent[0])
    for attribute in ('IconRes','SelectedIconRes'):
        if not template.get(attribute):
            print(f'Warning: base MenuData template has no {attribute}; menu items may show no icon')
    for child in list(parent):parent.remove(child)
    for p in profiles:
        item=copy.deepcopy(template)
        for child in list(item):item.remove(child)
        item.attrib.update(ItemId=p['id'],Value=p['id'],Title=p['name'],DisplayName=p['name'],
                           ExecType='SET_VALUE',NextMenuID='')
        parent.append(item)
    tree.write(path,encoding='utf-8',xml_declaration=True)
    # RAW+JPEG / RAW quality entries and the controller hook that unlocks them.
    patch_quality_menu(base)
    patch_quality_controller(base)
    patch_strength_menu(base, OLD+'.shooting.camera.PictureEffectPlusController')
    ctrl=base/'smali'/OLD.replace('.','/')/'shooting/camera/PictureEffectPlusController.smali'
    text=ctrl.read_text(encoding='utf-8')
    for name in ('getSupportedValue','getAvailableValue'):
        sig=name+'(Ljava/lang/String;)Ljava/util/List;'
        method=f'''.method public {sig}
    .locals 1
    const-string v0, "FujiStrength"
    invoke-virtual {{v0, p1}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-eqz v0, :not_strength
    invoke-static {{}}, {HOOK}->getStrengthValues()Ljava/util/List;
    move-result-object v0
    return-object v0
    :not_strength
    const-string v0, "ApplicationTop"
    invoke-virtual {{v0, p1}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-nez v0, :film_list
    const-string v0, "PictureEffect"
    invoke-virtual {{v0, p1}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-nez v0, :film_list
    invoke-super {{p0, p1}}, Lcom/sony/imaging/app/base/shooting/camera/PictureEffectController;->{sig}
    move-result-object v0
    return-object v0
    :film_list
    invoke-static {{}}, {HOOK}->getPresetIds()Ljava/util/List;
    move-result-object v0
    return-object v0
.end method'''
        text=replace_method(text,sig,method)
    # Native PictureEffect availability can be false for RAW or movie mode.
    # Our matrix/gamma presets do not use that native effect. Keep their menu
    # available while idle, but do not allow changes during video recording.
    for name, result in [('isAvailable', 'available'),
                         ('isUnavailableSceneFactor', 'scene')]:
        sig=name+'(Ljava/lang/String;)Z'
        custom_return = '''    invoke-static {}, Lcom/sony/imaging/app/base/shooting/movie/MovieShootingExecutor;->isMovieRecording()Z
    move-result v0
    xor-int/lit8 v0, v0, 0x1
    return v0''' if result == 'available' else '''    const/4 v0, 0x0
    return v0'''
        text=replace_method(text,sig,f'''.method public {sig}
    .locals 1
    const-string v0, "FujiStrength"
    invoke-virtual {{v0, p1}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-nez v0, :fuji_menu
    const-string v0, "ApplicationTop"
    invoke-virtual {{v0, p1}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-nez v0, :fuji_menu
    const-string v0, "PictureEffect"
    invoke-virtual {{v0, p1}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-nez v0, :fuji_menu
    invoke-static {{p1}}, {HOOK}->isRicohPreset(Ljava/lang/String;)Z
    move-result v0
    if-nez v0, :fuji_menu
    invoke-super {{p0, p1}}, Lcom/sony/imaging/app/base/shooting/camera/PictureEffectController;->{sig}
    move-result v0
    return v0
    :fuji_menu
{custom_return}
.end method''')
    # Handle the independent strength setting before the upstream effect logic.
    for name, signature, body in (
        ('getValue', 'getValue(Ljava/lang/String;)Ljava/lang/String;', f'''    invoke-static {{}}, {HOOK}->getStrengthValue()Ljava/lang/String;
    move-result-object v0
    return-object v0'''),
        ('setValue', 'setValue(Ljava/lang/String;Ljava/lang/String;)V', f'''    invoke-static {{p0, p2}}, {HOOK}->setStrengthValue({CTRL}Ljava/lang/String;)V
    return-void'''),
    ):
        method=re.search(r'^\.method public '+re.escape(signature)+r'\n[\s\S]*?^\.end method',text,re.M).group()
        prefix=f'''    const-string v0, "FujiStrength"
    invoke-virtual {{v0, p1}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-eqz v0, :fuji_original
{body}
    :fuji_original
'''
        assert method.count('    .prologue\n') == 1
        method=method.replace('    .prologue\n', '    .prologue\n'+prefix)
        text=replace_method(text,signature,method)
    ctrl.write_text(text,encoding='utf-8')
    layout=base/'smali'/OLD.replace('.','/')/'shooting/layout/PictureEffectPlusOptionMenuLayout.smali'
    cls='L'+OLD.replace('.','/')+'/shooting/layout/PictureEffectPlusOptionMenuLayout;'
    text=layout.read_text(encoding='utf-8')
    layout.write_text(replace_method(text,'initializeIconMap()V',
                                     icon_map_method(cls,text,profiles)),encoding='utf-8')
    if live:
        patch_option_menu_live(layout,scrim_alpha)
    fix_menu_key_exit(layout,cls)
    annotate_icons(layout,HOOK,debug)

def patch_movie(base, debug=False):
    patch_movie_menu(base)
    # Movie standby has a separate handler from still shooting. Open the same
    # effect chooser from center/enter without changing the recording handler.
    handler='Lcom/sony/imaging/app/base/shooting/movie/trigger/MovieRecStandbyStateKeyHandler;'
    path=base/'smali'/handler[1:-1]
    path=path.with_suffix('.smali')
    text=path.read_text(encoding='utf-8')
    assert '.method public pushedCenterKey()I' not in text
    text+=f'''
.method public pushedCenterKey()I
    .locals 3
    invoke-static {{}}, Lcom/sony/imaging/app/base/shooting/movie/MovieShootingExecutor;->isMovieRecording()Z
    move-result v0
    if-nez v0, :done
    new-instance v0, Landroid/os/Bundle;
    invoke-direct {{v0}}, Landroid/os/Bundle;-><init>()V
    const-string v1, "ItemId"
    const-string v2, "ApplicationTop"
    invoke-virtual {{v0, v1, v2}}, Landroid/os/Bundle;->putString(Ljava/lang/String;Ljava/lang/String;)V
    invoke-virtual {{p0, v0}}, {handler}->openMenu(Landroid/os/Bundle;)V
    const-string v0, "FujiMovieShortcut"
    const-string v1, "Movie standby center: opening filter menu"
    invoke-static {{v0, v1}}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I
    :done
    const/4 v0, 0x1
    return v0
.end method
'''
    for name in ('pushedEnter5WayFuncKey', 'pushedEnterJoyStickFuncKey'):
        assert f'.method public {name}()I' not in text
        text+=f'''
.method public {name}()I
    .locals 1
    invoke-virtual {{p0}}, {handler}->pushedCenterKey()I
    move-result v0
    return v0
.end method
'''
    path.write_text(text,encoding='utf-8')
    app=base/'smali'/OLD.replace('.','/')/'PictureEffectPlus.smali'
    app.write_text(replace_method(app.read_text(encoding='utf-8'),'getSupportingRecMode()I',
        '.method public getSupportingRecMode()I\n    .locals 1\n    const/4 v0, 0x3\n    return v0\n.end method'),encoding='utf-8')
    # Use Sony's existing mode-aware exposure controller when movie mode is enabled.
    exposure=base/'smali'/OLD.replace('.','/')/'shooting/camera/PictureEffectPlusExposureModeController.smali'
    text=exposure.read_text(encoding='utf-8')
    for name in ('getSupportedValue','getAvailableValue'):
        sig=name+'(Ljava/lang/String;)Ljava/util/List;'
        text=replace_method(text,sig,f'''.method public {sig}
    .locals 1
    invoke-super {{p0, p1}}, Lcom/sony/imaging/app/base/shooting/camera/ExposureModeController;->{sig}
    move-result-object v0
    return-object v0
.end method''')
    for signature, target in (
        ('getCautionId()I', 'getCautionId()I'),
        ('isValidDialPosition()Z', 'isValidDialPosition()Z'),
        ('isValidExpoMode(Ljava/lang/String;)Z', 'isValidValue(Ljava/lang/String;)Z'),
    ):
        args = 'p0, p1' if 'String' in signature else 'p0'
        text=replace_method(text,signature,f'''.method public {signature}
    .locals 1
    invoke-super {{{args}}}, Lcom/sony/imaging/app/base/shooting/camera/ExposureModeController;->{target}
    move-result v0
    return v0
.end method''')
    text=replace_method(text,'isValidDialPosition(I)Z','''.method public isValidDialPosition(I)Z
    .locals 1
    invoke-static {p1}, Lcom/sony/imaging/app/base/shooting/camera/ExposureModeController;->scancode2Value(I)Ljava/lang/String;
    move-result-object v0
    if-eqz v0, :invalid
    invoke-super {p0, v0}, Lcom/sony/imaging/app/base/shooting/camera/ExposureModeController;->isValidValue(Ljava/lang/String;)Z
    move-result v0
    return v0
    :invalid
    const/4 v0, 0x0
    return v0
.end method''')
    exposure.write_text(text,encoding='utf-8')
    path=base/'smali/com/sony/imaging/app/base/shooting/movie/MovieShootingExecutor$MovieRecStartRunnable.smali'
    text=path.read_text(encoding='utf-8')
    needle='    sget-object v2, Lcom/sony/imaging/app/base/shooting/movie/MovieShootingExecutor;->sMediaRecorder:Lcom/sony/scalar/media/MediaRecorder;'
    assert text.count(needle)==1
    # --debug only: logMovieSettings() re-enters MovieFormatController from
    # inside the recorder start path, so keep it out of release builds.
    log_settings = f'    invoke-static {{}}, {HOOK}->logMovieSettings()V\n' if debug else ''
    prefix=log_settings+f'''    invoke-static {{}}, {HOOK}->ensureForMovie()Z
    move-result v2
    if-nez v2, :fuji_movie_ready
    new-instance v2, Ljava/lang/IllegalStateException;
    const-string v3, "Film filter was not applied"
    invoke-direct {{v2, v3}}, Ljava/lang/IllegalStateException;-><init>(Ljava/lang/String;)V
    throw v2
    :fuji_movie_ready
'''
    path.write_text(text.replace(needle,prefix+needle),encoding='utf-8')
    # The upstream callback logs "MovieRecError" even when error == 0.
    # Record the actual nonzero code so it cannot be confused with failure.
    path=base/'smali/com/sony/imaging/app/base/shooting/movie/MovieShootingExecutor$MovieRecErrorRunnable.smali'
    text=path.read_text(encoding='utf-8')
    needle='    .line 465\n'
    assert text.count(needle)==1
    diagnostic='''    iget v1, p0, Lcom/sony/imaging/app/base/shooting/movie/MovieShootingExecutor$MovieRecErrorRunnable;->mError:I
    if-eqz v1, :fuji_error_checked
    const-string v2, "FujiMovieErrorCode"
    invoke-static {v1}, Ljava/lang/Integer;->toString(I)Ljava/lang/String;
    move-result-object v3
    invoke-static {v2, v3}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;)I
    :fuji_error_checked
'''
    path.write_text(text.replace(needle,diagnostic+needle),encoding='utf-8')
    # Read back recorder parameters after a menu change; never alter them here.
    path=base/'smali/com/sony/imaging/app/base/shooting/camera/MovieFormatController.smali'
    text=path.read_text(encoding='utf-8')
    sig='setValue(Ljava/lang/String;Ljava/lang/String;)V'
    method=re.search(r'^\.method public '+re.escape(sig)+r'\n[\s\S]*?^\.end method',text,re.M).group()
    if debug:
        method=method.replace('    return-void',f'    invoke-static {{}}, {HOOK}->logMovieSettings()V\n    return-void')
    path.write_text(replace_method(text,sig,method),encoding='utf-8')

def restore_stub_drawables(base):
    """Replace 1x1 placeholder PNGs with the real artwork.

    The base APK ships every picture-effect icon, preview image and launcher
    icon as a 1x1 black PNG in drawable-long-nodpi, with the real image only in
    drawable-notlong-nodpi. Android picks exactly one of the two for a given
    device, so bodies whose screen is classified "long" (a7R II) render every
    icon as an empty box while "notlong" bodies (a5100) look fine. Copying the
    real image over the placeholder fixes the long case and leaves the other
    untouched.
    """
    res=base/'res'
    def png_size(path):
        data=path.read_bytes()[:24]
        if len(data) < 24 or data[:8] != b'\x89PNG\r\n\x1a\n':
            return None
        return struct.unpack('>II',data[16:24])
    donors={}
    source=res/'drawable-notlong-nodpi'
    if source.is_dir():
        for path in source.glob('*.png'):
            if png_size(path) not in (None,(1,1)):
                donors[path.name]=path
    restored=0
    for folder in res.glob('drawable*'):
        if not folder.is_dir() or folder.name=='drawable-notlong-nodpi':
            continue
        for path in folder.glob('*.png'):
            if png_size(path) == (1,1) and path.name in donors:
                shutil.copyfile(donors[path.name],path)
                restored+=1
    print(f'Restored {restored} placeholder drawables from drawable-notlong-nodpi')
    return restored

def rename_package(base,android_version):
    assert len(OLD)==len(NEW)
    for path in base.rglob('*'):
        if not path.is_file():continue
        if path.suffix in ('.smali','.xml','.arsc'):
            data=path.read_bytes()
            for old,new in [(OLD,NEW),(OLD.replace('.','/'),NEW.replace('.','/')),
                            ('理光相机',APP_NAME)]:
                for encoding in ('utf-8','utf-16le'):
                    a,b=old.encode(encoding),new.encode(encoding)
                    assert len(a)==len(b)
                    data=data.replace(a,b)
            if path.suffix=='.smali':
                data=data.replace(b'\\u7406\\u5149\\u76f8\\u673a',APP_NAME.encode('unicode_escape'))
            if path.name=='AndroidManifest.xml' and path.parent==base:
                data=axml_replace_string(data,ORIGINAL_ANDROID_VERSION,android_version)
            path.write_bytes(data)
    source=base/'smali'/OLD.replace('.','/')
    dest=base/'smali'/NEW.replace('.','/')
    dest.parent.mkdir(parents=True,exist_ok=True)
    source.rename(dest)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input',type=Path,required=True)
    ap.add_argument('--apktool',type=Path,required=True)
    ap.add_argument('--upstream-hook',type=Path,required=True)
    ap.add_argument('--work',type=Path,required=True)
    ap.add_argument('--movie',action='store_true',help='Enable experimental Sony movie path')
    ap.add_argument('--debug',action='store_true',help='Add FujiDiag logging for on-camera diagnosis')
    ap.add_argument('--native-preview',action='store_true',
                    help='Force the camera\'s own Creative Style even on bodies that report '
                         'RGB-matrix support (default: only when the body reports none)')
    ap.add_argument('--gate-extended-gamma',action='store_true',
                    help='Ask the body before writing the extended gamma table and skip it '
                         'when it reports no support. Off by default: the capability answers '
                         'are unreliable, and skipping the curve leaves the matrix alone')
    ap.add_argument('--probe-look',metavar='MODE[:EFFECT]',
                    help='Diagnostic build: give the first preset this live-view look so a '
                         'style/effect token can be checked on the camera at app start')
    ap.add_argument('--keep-sample-image',action='store_true',
                    help='Keep the static per-effect illustration in the filter chooser '
                         '(default: clear it so the live view shows through)')
    ap.add_argument('--menu-scrim',type=lambda value: int(value,0),default=DEFAULT_MENU_SCRIM,
                    metavar='ALPHA',
                    help='Alpha of the dark scrim behind the filter chooser (0x00 keeps the '
                         'menu transparent, 0xff is opaque black and hides the live view). '
                         f'Default 0x{DEFAULT_MENU_SCRIM:02x}')
    ap.add_argument('--leica-white',choices=('faithful','anchor'),default='faithful',
                    help='Leica tone treatment. faithful keeps the LUT\'s own tone, so '
                         'diffuse white renders near 0.75; anchor rescales the fitted tone '
                         'curve output so input 1.0 still maps to 1.0. Both share one matrix.')
    ap.add_argument('--no-leica',action='store_true',
                    help='Leave the Leica family out, for comparing against the previous release')
    args=ap.parse_args()
    root=Path(__file__).resolve().parents[1]
    if hashlib.sha256(args.input.read_bytes()).hexdigest()!=EXPECTED:
        raise SystemExit('Input hash mismatch: this patch requires the tested upstream v1.1.4 APK')
    if hashlib.sha256(args.upstream_hook.read_bytes()).hexdigest()!=EXPECTED_HOOK:
        raise SystemExit('Hook hash mismatch: use the pinned upstream revision in the installation guide')
    if args.work.exists() and any(args.work.iterdir()):
        raise SystemExit('Work directory is not empty. Choose a fresh directory; no existing files were removed.')
    for folder in ('profiles', 'output', 'validation'):
        (root/folder).mkdir(parents=True, exist_ok=True)
    if not (root/'profiles/fuji_official_approx.json').exists():
        raise SystemExit('No local profiles. Follow the rights/input checks and fit_luts.py step in docs/INSTALL.en.md.')
    fuji_data=json.loads((root/'profiles/fuji_official_approx.json').read_text(encoding='utf-8'))
    # The fitted matrices assume the neutral reference as their input, but the
    # camera feeds its own rendered Standard image, which is about 26% more
    # chromatic. Shipping the raw fit therefore overshoots saturation by ~41% at
    # 100% strength. tools/calibrate_camera.py scales the chroma response; refuse
    # to build parameters that were never calibrated.
    if 'domain_calibration' not in fuji_data:
        raise SystemExit('profiles/fuji_official_approx.json has no domain_calibration block: '
                         'the Fujifilm matrices would overshoot saturation on the camera. '
                         'Run tools/calibrate_camera.py . first.')
    fuji=fuji_data['presets']
    leica=[]
    if not args.no_leica:
        if not (root/LEICA_FILE).exists():
            raise SystemExit('No fitted Leica profiles. Run tools/fit_leica.py first, as in '
                             'docs/INSTALL.en.md.')
        leica=leica_profiles(root/LEICA_FILE,args.leica_white)
    profiles=combined_profiles(fuji,args.upstream_hook,leica)
    # The two Leica treatments must be distinguishable on the camera, because the
    # whole point of building both is to install one, shoot, then install the other.
    android_version=ANDROID_VERSION+('a' if (leica and args.leica_white=='anchor') else '')
    leica_suffix='' if not leica else ('-leica-anchor' if args.leica_white=='anchor' else '')
    # A probe build only exists to ask one camera body whether it accepts a
    # style or effect token. It swaps the look of the first preset, which the
    # app applies at every start, so no dialing is needed to reach it.
    build_profiles=profiles
    probe=''
    if args.probe_look:
        mode,_,effect=args.probe_look.partition(':')
        mode,effect=mode.strip(),effect.strip() or None
        if mode not in NATIVE_MODES or (effect and effect not in NATIVE_EFFECTS):
            raise SystemExit('Unknown style or effect token: '+args.probe_look)
        build_profiles=copy.deepcopy(profiles)
        build_profiles[0]['native']={'mode':mode,'picture_effect':effect}
        probe='-probe-'+mode+('-'+effect if effect else '')
        print('Probe look:',profiles[0]['id'],'->',mode,effect)
    subprocess.run(['java','-jar',str(args.apktool),'d','-r','-f',str(args.input),'-o',str(args.work)],check=True)
    patch_hook(args.work/'smali'/OLD.replace('.','/')/'shooting/camera/RicohHook.smali',build_profiles,args.upstream_hook,args.movie,args.debug,args.native_preview,args.gate_extended_gamma)
    patch_menu(args.work,build_profiles,args.debug,not args.keep_sample_image,args.menu_scrim)
    if args.movie:patch_movie(args.work,args.debug)
    restore_stub_drawables(args.work)
    rename_package(args.work,android_version)
    # Keep attribution and license scope with the installable artifact itself.
    legal=args.work/'assets/legal'
    legal.mkdir(parents=True,exist_ok=True)
    for source,name in (
        ('LICENSE','LICENSE-PolyForm-Noncommercial-1.0.0.txt'),
        ('LICENSES/Apache-2.0.txt','LICENSE-Apache-2.0.txt'),
        ('NOTICE','NOTICE.txt'),
        ('LICENSING.md','LICENSING.md'),
    ):
        shutil.copyfile(root/source,legal/name)
    unsigned=args.work.parent/'film-studio-unsigned.apk'
    subprocess.run(['java','-jar',str(args.apktool),'b',str(args.work),'-o',str(unsigned)],check=True)
    key=root/'.private/signing.pem'
    key.parent.mkdir(exist_ok=True)
    if not key.exists():
        generated,_=ensure_pem()
        shutil.move(generated,key)
        key.chmod(0o600)
    output=root/'output'/('FilmStudio-'+VERSION+('-movie' if args.movie else '-photo')
                          +leica_suffix
                          +('-native-forced' if args.native_preview else '')
                          +('-gamma-gated' if args.gate_extended_gamma else '')
                          +probe+('-debug' if args.debug else '')+'.apk')
    sign_apk(str(unsigned),str(output),str(key))
    with zipfile.ZipFile(output) as z:
        assert z.testzip() is None
        assert z.read('classes.dex')[:8]==b'dex\n035\0'
    metadata=dict(file=output.name,package=NEW,sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                  version=VERSION,movie_enabled=args.movie,debug_build=args.debug,
                  native_style_fallback=('forced' if args.native_preview else 'auto'),
                  extended_gamma=('capability-gated' if args.gate_extended_gamma else 'always'),
                  probe_look=args.probe_look,
                  live_filter_chooser=not args.keep_sample_image,
                  menu_scrim_alpha=args.menu_scrim,
                  camera_tested=False,encoded_video_filter_verified=False,
                  source_apk_sha256=EXPECTED,profiles=len(profiles),
                  app_name=APP_NAME,android_version=android_version,
                  leica_white=(args.leica_white if leica else None),
                  profile_families={'fujifilm':10,'ricoh':5,'leica':len(leica)})
    (root/'profiles/film_studio.json').write_text(json.dumps(dict(
        version=VERSION,android_version=android_version,
        leica_white=(args.leica_white if leica else None),
        presets=profiles),ensure_ascii=False,indent=2),encoding='utf-8')
    (root/'validation'/(output.stem+'.json')).write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    print('Built:',output)

if __name__=='__main__':main()
