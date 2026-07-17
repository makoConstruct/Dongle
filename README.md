> [!CAUTION]
> I disendorse this approach. This was originally written in response to difficulties with controlling centering and line height in flutter. It turns out that these aren't solvable in general by rectifying font metrics, due to the asymmetry between ascenders and descenders (which Dongle's latin characters don't have, but the korean characters and some of the accented capitals introduce one), they have to be solved per-font by adding font-specific interventions to your text widget in flutter, and, alas, different values are needed for different characters, lowercase is centered by default, but capitals and numerals need different tuning. See <https://github.com/makoConstruct/timer/>'s `BandCenteredText` to see the crude way we deal with this. The web, so much more ornate, has less crude ways via `text-box: trim` css.

## Dongle Latin

[Dongle(동글)](https://github.com/yangheeryu/Dongle) is a korean font, but it happens to contain a really good latin font as well. It turns out that korean fonts can't be centered on the line in the way that many programs will expect latin fonts to be, so latin users will have issues with that. So here is Dongle Latin. It strips out the korean characters, which reduces file size from 4.3 MB to 137KB, and it vertically recenters it. This conversion was performed using a rather incoherent Claude Opus 4.7, and the build_latin.py script it wrote. It could do with a proper inspection, but everything seems to work right.

> Dongle(동글) is a rounded sans-serif typeface for display. It is a modular Hangeul with the de-square frame, creating a playful and rhythmic movement. The name, Dongle(동글) comes from a Korean onomatopoeia, meaning 'rounded or curved shape(with adorable impression)’. 

> It is designed especially for Hangeul typography, but it also includes Latin alphabet as a part of KS X 1001. This typeface has a light, regular, and bold weight.

## Designer
Yanghee Ryu

## License
SIL Open Font License ([OFL.txt](OFL.txt))
