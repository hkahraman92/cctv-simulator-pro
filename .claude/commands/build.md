---
description: Windows exe derle ve Tcl/Tk teşhis dosyasını oku
argument-hint: "[onefile|onedir]"
---

`build_exe.bat $ARGUMENTS` çalıştır (argüman yoksa `onedir`).

Derleme bittiğinde — başarılı ya da başarısız — **her zaman** `build/tkinter-diagnostic.txt`
dosyasını oku ve özetle. CLAUDE.md kuralı: "Her derleme `build/tkinter-diagnostic.txt`
yazar. Tahmin etmeden önce onu oku."

Derleme başarısız olursa üç bilinen arıza sınıfını kontrol et (CLAUDE.md "Windows exe
derlemesi"):
1. `No module named 'tkinter'` — derleyen yorumlayıcıda tkinter yok
2. `Can't find a usable init.tcl` — `TCL_LIBRARY` kurulmamış
3. `invalid command name "::msgcat::mcmset"` — `msgcat` Tcl Modülü yolu

İnatçı sorunda önce `cctv_simulator_minimal.spec` (`console=True`, sıfır Tcl/Tk
müdahalesi) ile dene.

Anaconda ile derleme yapma (Tcl/Tk düşer). Yorumlayıcı: `py -3.13`.
