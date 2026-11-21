"""Provisional Tangut IDS glyphs; independently arrange pinned Noto outlines only."""

from hashlib import sha256
from io import BytesIO


def require(condition, message):
    if not condition:
        raise ValueError(message)


def derive(source, notice):
    from fontTools import __version__ as fonttools_version
    from fontTools.ttLib import TTFont
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen, replayRecording
    from fontTools.pens.transformPen import TransformPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.misc.transform import Transform
    require(fonttools_version == "4.61.1", "Tangut IDS trial requires fontTools 4.61.1")
    require(sha256(source).hexdigest() == "edcc5b7727fa4d1345d2fca93305c8b90ad88d4975152d4aed79564c9117db91", "Unexpected Noto source")
    require(sha256(notice).hexdigest() == "bd8b32c064be2225d55f878a689063c6f169ccd9e2792d7051f0d74cf4816f1b", "Unexpected Noto license")
    font=TTFont(BytesIO(source), recalcTimestamp=False); gs=font.getGlyphSet(); cmap=font.getBestCmap()
    def contours(code):
     p=DecomposingRecordingPen(gs); gs[cmap[code]].draw(p)
     buf=[]; current=[]
     for op,args in p.value:
      current.append((op,args))
      if op=='closePath':
       buf.append(current);current=[]
     require(not current, 'Open tangut component contour')
     return buf
    def flat(parts): return [p for c in parts for p in c]
    def fit(parts, box):
     coords=flat(parts); b=BoundsPen(None); replayRecording(coords,b)
     x0,y0,x1,y1=b.bounds; xmin,ymin,xmax,ymax=box
     a=(xmax-xmin)/(x1-x0);d=(ymax-ymin)/(y1-y0)
     dst=RecordingPen()
     replayRecording(coords,TransformPen(dst,Transform(a,0,0,d,xmin-a*x0,ymin-d*y0)))
     return dst.value
    whole=contours(0x17A11)
    require(len(whole) == 9, 'Unexpected pinned Tangut compound contour order')
    # N5303 documents that only the left component varies from U+17A11.
    # Preserve every contour of the compound right component from the OFL base.
    one=fit(contours(0x188D1),(31,-79,393,804))+flat([p for i,p in enumerate(whole) if i not in (1,4)])
    # N5314 establishes two left/right components. Keep similar stroke density.
    two=fit(contours(0x1886B),(54,-79,470,817))+fit(contours(0x18933),(485,-79,951,817))
    glyphs={'.notdef':TTGlyphPen(None).glyph()}
    for name,value in [('u18D1F',one),('u18D20',two)]:
     p=TTGlyphPen(None); replayRecording(value,p); glyphs[name]=p.glyph()
    builder=FontBuilder(1000,isTTF=True)
    builder.setupGlyphOrder(list(glyphs));builder.setupCharacterMap({0x18D1F:'u18D1F',0x18D20:'u18D20'})
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name:(1000,g.xMin if hasattr(g,'xMin') else 0) for name,g in glyphs.items()})
    builder.setupHorizontalHeader(ascent=856,descent=-150)
    ofl = notice.decode("utf-8").replace("\r\n","\n")
    builder.setupNameTable({'familyName':'FontRoutes Tangut IDS Trial','styleName':'Regular','uniqueFontIdentifier':'FontRoutesTangutIDSTrial-Regular-1.0','fullName':'FontRoutes Tangut IDS Trial Regular','psName':'FontRoutesTangutIDSTrial-Regular','version':'Version 1.0','copyright':ofl.splitlines()[0]+'; independently arranged component layouts by FontRoutes contributors','licenseDescription':ofl,'licenseInfoURL':'https://openfontlicense.org/open-font-license-official-text/'})
    builder.setupOS2(sTypoAscender=856,sTypoDescender=-150,usWinAscent=856,usWinDescent=150)
    builder.setupPost();builder.font['head'].created=builder.font['head'].modified=font['head'].created
    builder.font.recalcTimestamp=False
    output = BytesIO()
    builder.save(output)
    binary = output.getvalue()
    with TTFont(BytesIO(binary), recalcTimestamp=False) as built:
     wanted = {0x18D1F: 'u18D1F', 0x18D20: 'u18D20'}
     require(built.getBestCmap() == wanted and all(t.cmap.items() <= wanted.items()
               for t in built["cmap"].tables if t.isUnicode()), "Unexpected rendered Tangut cmap")
     require(set(built.getGlyphOrder()) == {'.notdef', *wanted.values()}, "Unexpected derived Tangut glyph order")
     for name in wanted.values():
      pen = BoundsPen(built.getGlyphSet())
      built.getGlyphSet()[name].draw(pen)
      require(pen.bounds and pen.bounds[2] > pen.bounds[0] and pen.bounds[3] > pen.bounds[1], "Empty Tangut outline")
    return binary
