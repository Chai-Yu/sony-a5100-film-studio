#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Original verification logic only; third-party rights remain separate.
"""Check arrays and menu IDs after round-trip decompilation of the built APK."""
import argparse
import json
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET
from axml_strings import strings as manifest_strings
from build_apk import ANDROID_VERSION, ORIGINAL_ANDROID_VERSION, read_icon_map
from film_profiles import FUJIFILM_PRESETS, PRESET_COUNT, read_array, ricoh_profiles
from filter_strength import DEFAULT_STRENGTH, STRENGTHS, blend_profile

ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = 'smali/com/yuki/imaging/app/pictureeffectplus/shooting/camera/RicohHook.smali'
LAYOUT_PATH = 'smali/com/yuki/imaging/app/pictureeffectplus/shooting/layout/PictureEffectPlusOptionMenuLayout.smali'


def icon_ids(layout_text):
    """item ID -> thumbnail resource ID, from the compiled icon map."""
    found = re.search(r'^\.method private initializeIconMap\(\)V\n[\s\S]*?^\.end method',
                      layout_text, re.M)
    assert found, 'initializeIconMap()V missing'
    # The map parser is shared with the builder, so both sides always read an
    # entry the same way; the anchor above still pins it to this one method.
    return read_icon_map(found.group())


def fields(text):
    links = re.findall(
        r'fill-array-data v1, :(\w+)\s+sput-object v1, [^\n]+->(sFuji\w+):(\[[IB])', text)
    result = {}
    for label, name, kind in links:
        data = read_array(text, label, 1 if kind == '[B' else 4, 2048 if kind == '[B' else 9)
        result[name] = [v & 255 for v in data] if kind == '[B' else data
    if len(result) != len(links):
        raise ValueError('Duplicate initialized fields')
    return result


def colour_forms(argb):
    """Every spelling apktool may use for one 32-bit colour constant.

    It writes plain hex for small values and the signed form (-0x...) once the
    high bit is set, which is the normal case for an ARGB colour with alpha.
    """
    forms = ['0x%X' % argb, '0x%08x' % argb]
    if argb & 0x80000000:
        magnitude = 0x100000000 - argb
        forms += ['-0x%X' % magnitude, '-0x%08x' % magnitude]
    return forms


def method_body(source, head):
    """Instructions of the first method named <head>, stripped and without blanks."""
    found = re.search(r'^\.method [^\n]* ' + head + r'\([^\n]*\n[\s\S]*?^\.end method',
                      source, re.M)
    assert found, head + ' missing'
    return [line.strip() for line in found.group().splitlines() if line.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--decoded', type=Path, required=True,
                    help='Fresh apktool d -r output from the final signed APK')
    ap.add_argument('--input-apk', type=Path,
                    help='The unmodified base APK; the patch rewrites the menu, so the '
                         'picture effect IDs have to be checked against the input')
    ap.add_argument('--upstream-hook', type=Path, required=True)
    ap.add_argument('--previous-decoded', type=Path,
                    help='Optional decoded 0.1.3 build, to compare existing arrays')
    ap.add_argument('--native-fallback', choices=('auto', 'forced'), default='auto',
                    help='auto: the hook only applies the camera style when the body '
                         'reports no RGB-matrix support; forced: it always applies it')
    ap.add_argument('--extended-gamma', choices=('always', 'gated'), default='always',
                    help='always (default): the hook writes the extended gamma table with the '
                         'matrix on every body, as upstream does; gated: it asks the body first '
                         'and skips the table when the body reports no support')
    ap.add_argument('--live-menu', choices=('on', 'off'), default='on',
                    help='on: the filter chooser clears the static effect illustration so '
                         'the running preview stays visible')
    ap.add_argument('--menu-scrim', type=lambda value: int(value, 0), default=0x00, metavar='ALPHA',
                    help='Alpha the build used for the optional list-panel background behind '
                         'the filter chooser; it must match what the builder wrote into '
                         'onCreateView (0 = fully transparent, the default)')
    args = ap.parse_args()
    build = json.loads((ROOT/'profiles/film_studio.json').read_text(encoding='utf-8'))
    profiles = build['presets']
    hook = (args.decoded/HOOK_PATH).read_text(encoding='utf-8')
    arrays = fields(hook)
    assert len(profiles) == PRESET_COUNT
    assert len(arrays) == PRESET_COUNT * len(STRENGTHS) * 2
    for i, p in enumerate(profiles):
        for strength in STRENGTHS:
            expected = blend_profile(p, strength)
            assert arrays[f'sFujimatrix{i}_{strength}'] == sum(expected['matrix'], [])
            raw = [b for v in expected['gamma'] for b in (v & 255, v >> 8)]
            assert arrays[f'sFujigamma{i}_{strength}'] == raw
    # Independent upstream input: verify both tinted and neutral presets retain
    # exact original values in the built DEX at the 100% endpoint.
    for i, p in enumerate(ricoh_profiles(args.upstream_hook), 10):
        assert profiles[i]['id'] == p['id']
        assert arrays[f'sFujimatrix{i}_100'] == sum(p['matrix'], [])
        assert arrays[f'sFujigamma{i}_100'] == [b for v in p['gamma'] for b in (v & 255, v >> 8)]
    # The strength menu offers every entry of STRENGTHS, and getStrength() picks
    # the compiled array set the hook reads. A whitelist that omits a choice
    # silently returns the fallback for it, which is how selecting 100% used to
    # deliver 50%, so the accepted values and the fallback are checked apart.
    strength_lines = method_body(hook, 'getStrength')
    accepted, fallback = set(), set()
    for index, line in enumerate(strength_lines):
        value = re.fullmatch(r'const/16 v\d+, (0x[0-9a-f]+)', line)
        if not value:
            continue
        following = strength_lines[index + 1] if index + 1 < len(strength_lines) else ''
        (accepted if 'if-eq' in following else fallback).add(int(value.group(1), 16))
    assert accepted == set(STRENGTHS), f'getStrength() rejects {sorted(set(STRENGTHS) - accepted)}'
    assert fallback == {DEFAULT_STRENGTH}, \
        f'getStrength() falls back to {sorted(fallback)}, not {DEFAULT_STRENGTH}'
    listed = re.findall(r'const-string v\d+, "(\d+)"',
                        '\n'.join(method_body(hook, 'getStrengthValues')))
    assert [int(s) for s in listed] == list(STRENGTHS), 'strength menu choices changed'
    menu = ET.parse(args.decoded/'assets/MenuData.xml')
    top = next(e for e in menu.iter() if e.get('ItemId') == 'ApplicationTop')
    ids = [p['id'] for p in profiles]
    assert [e.get('ItemId') for e in top] == ids
    assert [e.get('Value') for e in top] == ids
    # RAW capture: the still-image quality menu must expose both new entries,
    # carrying the short value the controller forwards to CameraSetting.
    quality = next((e for e in menu.iter()
                    if e.tag == 'Layer1' and e.get('ItemId') == 'setPictureStorageFormat'), None)
    assert quality is not None, 'setPictureStorageFormat menu missing'
    quality_items = {e.get('ItemId'): e.get('Value') for e in quality}
    assert quality_items.get('setPictureStorageFormat_rawjpeg') == 'rawjpeg', \
        'RAW+JPEG entry missing or wrong value'
    assert quality_items.get('setPictureStorageFormat_raw') == 'raw', \
        'RAW entry missing or wrong value'
    for item_id in ('setPictureStorageFormat_rawjpeg', 'setPictureStorageFormat_raw'):
        entry = next(e for e in quality if e.get('ItemId') == item_id)
        assert entry.get('ConfigClass') == \
            'com.sony.imaging.app.base.shooting.camera.PictureQualityController', item_id
    # Every preset needs a thumbnail that the base APK really defined, and the
    # menu entries must still carry the icon names the menu renderer reads.
    icons = icon_ids((args.decoded/LAYOUT_PATH).read_text(encoding='utf-8'))
    assert all(icons.get(preset_id) for preset_id in ids), 'preset without a thumbnail'
    kept = len(icons) - len(ids)
    for e in top:
        assert e.get('IconRes') and e.get('SelectedIconRes'), e.get('ItemId')
    # A 1x1 placeholder left behind would render as an empty icon on devices
    # whose screen is classified "long".
    def png_size(data):
        if len(data) < 24 or data[:8] != b'\x89PNG\r\n\x1a\n':
            return None
        return struct.unpack('>II', data[16:24])
    res = args.decoded/'res'
    placeholders = 0
    if res.is_dir():
        real = {p.name for p in (res/'drawable-notlong-nodpi').glob('*.png')
                if png_size(p.read_bytes()) not in (None, (1, 1))}
        for folder in res.glob('drawable*'):
            if folder.is_dir() and folder.name != 'drawable-notlong-nodpi':
                placeholders += sum(1 for p in folder.glob('*.png')
                                    if p.name in real and png_size(p.read_bytes()) == (1, 1))
        assert placeholders == 0, f'{placeholders} placeholder drawables left'
    for method in ['getPresetIds', 'getRGBMatrix', 'getGammaBytes', 'getFilterName', 'getFilterGuide']:
        body = re.search(r'^\.method [^\n]* ' + method + r'\([^\n]*\n(.*?)^\.end method', hook, re.M | re.S)
        assert body, method
        for preset_id in ids:
            assert '"' + preset_id + '"' in body[1], (method, preset_id)
    # The two RAW ItemIds must resolve to a label/guide like every other entry,
    # so the quality menu shows a name instead of a blank or a raw token.
    for item_id in ('setPictureStorageFormat_rawjpeg', 'setPictureStorageFormat_raw'):
        name_body = method_body(hook, 'getFilterName')
        guide_body = method_body(hook, 'getFilterGuide')
        assert '"' + item_id + '"' in '\n'.join(name_body), ('getFilterName', item_id)
        assert '"' + item_id + '"' in '\n'.join(guide_body), ('getFilterGuide', item_id)
    # The quality-availability filter must exist exactly once, with the upstream
    # signature, and must force both RAW values on while passing others through.
    filters = re.findall(
        r'^\.method public static filterQualityAvailability\(Ljava/lang/String;Z\)Z\n'
        r'[\s\S]*?^\.end method', hook, re.M)
    assert len(filters) == 1, 'filterQualityAvailability missing or duplicated'
    fbody = filters[0]
    assert '"rawjpeg"' in fbody and '"raw"' in fbody, 'RAW values not whitelisted'
    assert 'return p1' in fbody, 'non-RAW values must pass the original answer through'
    # The injection point in PictureQualityController must exist exactly once and
    # call the hook after reading AvailableInfo's answer.
    pqc_path = (args.decoded/'smali/com/sony/imaging/app/base/shooting/camera/'
                'PictureQualityController.smali')
    assert pqc_path.exists(), 'PictureQualityController.smali missing from build'
    pqc = pqc_path.read_text(encoding='utf-8')
    assert pqc.count('->filterQualityAvailability(') == 1, \
        'filterQualityAvailability injection missing or duplicated'
    assert re.search(r'->isAvailable\(\[Ljava/lang/Object;\)Z\s*\n\s*move-result v6\s*\n\s*'
                     r'invoke-static \{v3, v6\}, [^\n]*->filterQualityAvailability'
                     r'\(Ljava/lang/String;Z\)Z\s*\n\s*move-result v6', pqc), \
        'filterQualityAvailability not wired after isAvailable'
    # Live preview: a body whose ISP drops the RGB matrix shows nothing from the
    # hardware look, and then the camera's own Creative Style and Picture Effect
    # are the only looks that reach the screen. Both tokens must be ones this
    # app generation already ships, and the compiled hook must map every preset
    # onto them.
    styles = set(re.findall(
        r'\.field public static final \w+:Ljava/lang/String; = "([a-z-]+)"',
        (args.decoded / 'smali/com/sony/imaging/app/base/shooting/camera/CreativeStyleController.smali'
         ).read_text(encoding='utf-8')))
    assert styles, 'Creative Style tokens not found in the decoded build'
    # The patch replaces the base APK's own effect list under the same menu node,
    # so the shipped file no longer lists them: read the input APK instead. These
    # are the tokens the base app itself hands to setPictureEffect on this
    # camera generation.
    effect_ids = set()
    if args.input_apk:
        import zipfile
        effect_ids = set(re.findall(
            r'ItemId="([a-z0-9-]+)"',
            zipfile.ZipFile(args.input_apk).read('assets/MenuData.xml').decode('utf-8')))
        assert effect_ids, 'picture effect IDs not found in the input APK'
    for p in profiles:
        assert p['native']['mode'] in styles, (p['id'], p['native']['mode'])
        effect = p['native']['picture_effect']
        if effect is not None and effect_ids:
            assert effect in effect_ids, (p['id'], effect)
    native = re.search(r'^\.method public static applyNative\([\s\S]*?^\.end method', hook, re.M)
    assert native, 'applyNative(Lcom/sony/scalar/hardware/CameraEx$ParametersModifier;Ljava/lang/String;)V missing'
    native_body = native.group()
    assert '->setColorMode(' in native_body, 'applyNative never sets the style'
    for p in profiles:
        assert '"' + p['id'] + '"' in native_body, (p['id'], 'preset id missing from applyNative')
        assert '"' + p['native']['mode'] + '"' in native_body, (p['id'], 'style token missing from applyNative')
        effect = p['native']['picture_effect']
        if effect:
            assert '"' + effect + '"' in native_body, (p['id'], 'picture effect missing from applyNative')
    assert '->setPictureEffect(' in native_body, 'applyNative never sets the effect'
    # Every branch writes the effect, "off" included. A branch that wrote only
    # the style would leave the previous filter's effect on screen, and that
    # effect's own colour cast would be read as part of the filter just picked.
    assert '"off"' in native_body, 'applyNative never turns the effect off'
    assert native_body.count('->setPictureEffect(') == len(profiles), \
        'every preset branch must write the picture effect, not only the ones with one'
    assert '->applyNative(' in hook, 'applyHook never calls applyNative'
    # The capability query sits in its own guarded helper: a body that throws
    # from it would otherwise abort applyHook and drop the hardware look.
    helper = re.search(r'^\.method public static needsNative\([\s\S]*?^\.end method', hook, re.M)
    apply_hook = re.search(r'^\.method public static applyHook\([\s\S]*?^\.end method', hook, re.M)
    assert apply_hook, 'applyHook missing'
    # Extracted once: the gamma gate below and the effect reset further down
    # both read the same method.
    reset = re.search(r'^\.method public static resetHook\([\s\S]*?^\.end method', hook, re.M)
    assert reset, 'resetHook missing'
    auto_gate = '->needsNative(' in apply_hook.group()
    if args.native_fallback == 'auto':
        assert auto_gate, 'auto fallback must ask the body for RGB-matrix support'
        assert helper and '->isRGBMatrixSupported()Z' in helper.group(), 'needsNative helper missing'
        assert '->isRGBMatrixSupported()Z' not in apply_hook.group(), \
            'capability query must not run unguarded in applyHook'
        # Answering false for a body that reports no support would skip the
        # fallback exactly where it is needed, and would instead apply the
        # camera style on the bodies that do support the matrix.
        assert re.search(r'isRGBMatrixSupported\(\)Z\s*\n\s*move-result v(\d+)\s*\n\s*'
                         r'if-eqz v\1, :\w+\s*\n\s*const/4 v\1, 0x1\s*\n\s*return v\1',
                         helper.group()), \
            'needsNative must return true when the body reports no RGB-matrix support'
        assert re.search(r':\w+\s*\n\s*const/4 v\d+, 0x0\s*\n\s*return v\d+', helper.group()), \
            'needsNative must return false for a body that reports support'
        assert re.search(r'needsNative\([^)]*\)Z\s*\n\s*move-result v(\d+)\s*\n\s*'
                         r'if-eqz v\1, :\w+\s*\n\s*invoke-static \{[^}]*\}, [^\n]*->applyNative\(',
                         apply_hook.group()), \
            'applyHook must apply the camera style only when needsNative() answered true'
        # ...but the matrix is written whatever that answer was. A body that
        # reports no support still applies it, and suppressing it would suppress
        # the look: the capability answers are not trustworthy.
        assert '->setRGBMatrix([I)V' in apply_hook.group(), \
            'applyHook must always write the RGB matrix'
    # The extended gamma table is hardware the body may not have: creating and
    # clearing it still enters the native camera path, and resetHook runs from
    # the shooting state's onPause, i.e. on every MENU press.
    if args.extended_gamma == 'gated':
        assert re.search(r'isExtendedGammaTableSupported\(\)Z\s*\n\s*move-result v(\d+)'
                         r'[\s\S]{0,600}?if-eqz v\1, :\w+\s*\n\s*'
                         r'invoke-static \{p0\}[^\n]*->getCameraEx\(', apply_hook.group()), \
            'applyHook must ask the body before it builds an extended gamma table'
        assert re.search(r'setExtendedGammaTable\(Lcom/sony/scalar/hardware/CameraEx\$GammaTable;\)V'
                         r'\s*\n\s*const/4 v\d+, 0x1\s*\n\s*sput-boolean v\d+, [^\n]*->sExtendedGammaActive:Z',
                         apply_hook.group()), \
            'applyHook must record that it committed an extended gamma table'
        # Read the flag, let the clear be skipped when nothing was committed,
        # and write the flag back so the next apply starts from a known state.
        # Which of the two comes first is left to the builder.
        assert re.search(r'sget-boolean v(\d+), [^\n]*->sExtendedGammaActive:Z'
                         r'[\s\S]{0,240}?if-eqz v\1, :\w+'
                         r'[\s\S]{0,480}?setExtendedGammaTable'
                         r'\(Lcom/sony/scalar/hardware/CameraEx\$GammaTable;\)V', reset.group()), \
            'resetHook must clear only the extended gamma table that was committed'
        assert re.search(r'sput-boolean v\d+, [^\n]*->sExtendedGammaActive:Z', reset.group()), \
            'resetHook must clear the committed-gamma flag'
    else:
        # Default: the table is committed with the matrix on every body, exactly
        # as upstream does. The capability answers cannot decide it - an a7R II
        # reports no support for both and still applies the matrix - and gating
        # only the curve would leave a bare matrix that shifts hue on saturated
        # colour instead of rendering the look.
        assert 'isExtendedGammaTableSupported()Z' not in apply_hook.group(), \
            'the gamma table must not be gated on a capability answer the body gets wrong'
        assert re.search(r'setExtendedGammaTable\(Lcom/sony/scalar/hardware/CameraEx\$GammaTable;\)V',
                         apply_hook.group()), \
            'applyHook must commit the extended gamma table with the matrix'
    # The chooser must not paint the static illustration over the live preview:
    # both setBackgroundResource() sites have to be fed a cleared value.
    chooser = (args.decoded/LAYOUT_PATH).read_text(encoding='utf-8')
    cleared = re.findall(
        r'getBackgroundDrawable\(Ljava/lang/String;\)I\n\s*move-result v1\n\s*const/4 v1, 0x0\n',
        chooser)
    if args.live_menu == 'on':
        assert len(cleared) == 2, f'live chooser patch missing ({len(cleared)}/2 sites)'
        assert chooser.count('->setBackgroundResource(I)V') == 3, 'unexpected chooser background sites'
        # The layout root paints @android:color/black over the transparent
        # window, which would hide the preview again; it has to stay cleared.
        assert re.search(re.escape('mCurrentView:Landroid/view/ViewGroup;') + r'\s*\n\s*'
                         + re.escape('const/4 v0, 0x0') + r'\s*\n\s*'
                         + re.escape('invoke-virtual {v1, v0}, Landroid/view/View;->'
                                     'setBackgroundColor(I)V'),
                         chooser), 'menu root background is not cleared'
        # The readable dark background, when requested, must sit on the list
        # panel; with the default 0 the panels stay transparent like the root.
        scrim = r'const(?:/4|/16|/high16)? v0, (?:%s)' % '|'.join(
            re.escape(form) for form in colour_forms(args.menu_scrim << 24))
        for panel in ('mViewArea:Lcom/sony/imaging/app/base/menu/layout/SpecialScreenArea;',
                      'mSpecialScreenView:Lcom/sony/imaging/app/base/menu/layout/SpecialScreenView;'):
            assert re.search(re.escape(panel) + r'\s*\n\s*' + scrim + r'\s*\n\s*'
                             + re.escape('invoke-virtual {v1, v0}, Landroid/view/View;->'
                                         'setBackgroundColor(I)V'),
                             chooser), 'list panel is not dark: ' + panel
    else:
        assert not cleared, 'chooser still clears the illustration in --live-menu off'
    # MENU has to leave the chooser that the center key opened as well. That
    # path has no menu history, so it must reach the state-machine notification
    # through openPreviousMenu() instead of closing the layout on its own, which
    # left the shooting state stranded and made every later key a no-op.
    menu_exit = re.search(r'\.method public pushedMenuKey\(\)I\n[\s\S]*?^\.end method',
                          chooser, re.M).group()
    assert re.search(r'invoke-virtual \{p0\}, [^\n]*BaseMenuLayout;->openPreviousMenu\(\)V', menu_exit), \
        'pushedMenuKey must notify the menu state when it has no menu history'
    assert '->closeLayout()V' not in menu_exit, \
        'pushedMenuKey still closes the layout without notifying the menu state'
    # Leaving the app has to take the fallback effect back off, otherwise the
    # camera keeps rendering it after the filter was abandoned.
    assert '->setPictureEffect(' in reset.group(), 'resetHook never clears the picture effect'
    # ...and it has to use its own register for that: the token the following
    # setColorMode() reads must not be overwritten with "off".
    style_token = re.search(r'const-string v(\d+), "standard"', reset.group())
    assert style_token, 'resetHook does not restore the neutral style'
    style_register = style_token.group(1)
    assert re.search(r'invoke-virtual \{[^,]+, v' + style_register + r'\}, [^\n]*->setColorMode\(',
                     reset.group()), 'resetHook does not pass the neutral style to setColorMode()'
    effect_token = re.search(r'const-string v(\d+), "off"\s*\n\s*'
                             r'invoke-virtual \{[^,]+, v(\d+)\}, [^\n]*->setPictureEffect\(',
                             reset.group())
    assert effect_token, 'resetHook does not clear the picture effect'
    assert effect_token.group(1) == effect_token.group(2), \
        'resetHook clears the picture effect with a token from another register'
    assert effect_token.group(1) != style_register, \
        'resetHook clears the picture effect with the register setColorMode() reads'
    assert re.search(r'sget-boolean v(\d+), [^\n]*->sNativeEffectActive:Z\s*\n\s*if-eqz v\1, :\w+',
                     reset.group()), \
        'resetHook must clear only the picture effect that applyNative() actually set'
    movie = (args.decoded/'smali/com/sony/imaging/app/base/shooting/movie/trigger/MovieRecStandbyStateKeyHandler.smali').read_text(encoding='utf-8')
    assert 'pushedCenterKey()I' in movie and '"ApplicationTop"' in movie
    assert '->isMovieRecording()Z' in movie
    assert '->isMovieRecording()Z' in hook
    resources = (args.decoded/'resources.arsc').read_bytes()
    assert '胶片工坊'.encode() in resources
    for old in ['理光相机', '富士风格']:
        assert old.encode() not in resources and old.encode('utf-16-le') not in resources
    # apktool decodes with -r, so AndroidManifest.xml stays binary here and the
    # announced versionName has to be read out of its string pool. The base APK
    # ships a different version string, and the replacement is longer, so a
    # regression to a byte substitution would corrupt the manifest instead of
    # failing loudly; this is the check that notices.
    announced = manifest_strings(args.decoded/'AndroidManifest.xml')
    expected_version = build.get('android_version', ANDROID_VERSION)
    assert expected_version in announced, \
        f'manifest does not announce version {expected_version}'
    assert ORIGINAL_ANDROID_VERSION not in announced, \
        'manifest still carries the base APK version string'
    previous_count = None
    if args.previous_decoded:
        previous = fields((args.previous_decoded/HOOK_PATH).read_text(encoding='utf-8'))
        # Compare only the Fujifilm arrays: that family's fitted parameters must
        # survive a rebuild bit for bit. Naming them explicitly keeps this check
        # working when the previous build carried a different number of presets.
        shared = [f'sFuji{kind}{i}_{s}' for i in range(FUJIFILM_PRESETS)
                  for s in STRENGTHS for kind in ('matrix', 'gamma')]
        missing = [name for name in shared if name not in previous]
        assert not missing, f'previous build lacks {missing[:4]}'
        for name in shared:
            assert arrays[name] == previous[name], name
        previous_count = len(shared)
    report = dict(
        profiles=len(profiles), strengths=list(STRENGTHS), compiled_arrays_checked=len(arrays),
        upstream_ricoh_full_strength_exact=True, previous_fuji_arrays_unchanged=previous_count,
        menu_and_lookup_ids_match=True, preset_thumbnails=len(ids),
        base_thumbnails_kept=kept, placeholder_drawables=placeholders, renamed_resources=True,
        movie_standby_shortcut_present=True,
        native_style_tokens=sorted({p['native']['mode'] for p in profiles}),
        native_picture_effects=sorted({p['native']['picture_effect'] for p in profiles
                                       if p['native']['picture_effect']}),
        native_style_fallback=('runtime capability gate' if auto_gate else 'forced'),
        extended_gamma_table=('capability-gated' if args.extended_gamma == 'gated' else 'always'),
        native_effect_ids_checked_against_input=bool(effect_ids),
        manifest_version=expected_version,
        live_filter_chooser=(args.live_menu == 'on'),
        menu_scrim_alpha=args.menu_scrim,
        hardware_verified=False,
    )
    (ROOT/'validation/combined-static.json').write_text(json.dumps(report, indent=2),encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
