import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Info, Anchor, Activity, Download, Upload, Filter, Cpu, Waves, Database, GitBranch } from 'lucide-react';

export const SystemGuide: React.FC = () => {
  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <Card className="bg-white border-zinc-200 shadow-sm">
        <CardHeader>
          <CardTitle className="text-lg font-bold text-zinc-800 flex items-center gap-2">
            <Info className="w-5 h-5 text-blue-600" />
            ICR Soundbank Analyzer - Systemovy pruvodce
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-6 text-sm text-zinc-600 leading-relaxed">
          <section>
            <h3 className="text-sm font-bold text-zinc-800 mb-2 flex items-center gap-2">
              <Activity className="w-4 h-4 text-blue-600" />
              Prehled
            </h3>
            <p>
              ICR Soundbank Analyzer je nastroj pro analyzu, validaci a slucovani datasetu zvukovych bank
              pro aditivni a fyzikalni syntezu. Identifikuje anomalie, generuje optimalizovane kotevni body
              a umoznuje export vycistenych datasetu pro vysoce verne modely.
            </p>
          </section>

          <section>
            <h3 className="text-sm font-bold text-zinc-800 mb-2 flex items-center gap-2">
              <Database className="w-4 h-4 text-blue-600" />
              Banky a aktivni banka
            </h3>
            <p className="mb-2">
              V panelu repozitare lze nacist jednu nebo vice zvukovych bank. Kazda banka muze byt
              <strong> nactena</strong> (checkbox) a jedna z nich oznacena jako <strong>aktivni banka</strong> (zeleny
              radio button). Aktivni banka urcuje, ze ktere banky se berou kotevni body pri manualnim pridani.
            </p>
            <ul className="list-disc list-inside space-y-1 ml-2">
              <li><strong>Checkbox</strong> - Nacte/odebere banku z analyzy</li>
              <li><strong>Radio (zeleny kruh)</strong> - Nastavi banku jako zdroj pro kotevni body</li>
              <li>Badge <span className="text-emerald-700 font-semibold">ANCHOR SRC</span> oznacuje aktivni banku</li>
              <li>Pri nacteni prvni banky se automaticky nastavi jako aktivni</li>
            </ul>
          </section>

          <section>
            <h3 className="text-sm font-bold text-zinc-800 mb-2 flex items-center gap-2">
              <Cpu className="w-4 h-4 text-blue-600" />
              Fyzikalni synteza
            </h3>
            <p className="mb-2">
              Rezim fyzikalni syntezy pracuje s parametry modelovanych strun: zakladni frekvence (f0),
              inharmonicita (B), doba dozneni (T60), hmotnost kladivka, tuhost a dalsi.
            </p>
            <p className="mb-2">
              <strong>Physics-informed interpolace:</strong> Parametry jsou interpolovany mezi kotevnimi body
              s ohledem na fyzikalni zakony akustiky piana, zalozene na pracich
              Chabassier, Chaigne &amp; Joly (INRIA/ENSTA) a Simionato et al.
            </p>
            <ul className="list-disc list-inside space-y-1 ml-2">
              <li><strong>Log-space interpolace</strong> pro parametry, ktere se meni pres rady velikosti:
                f0, B, T60_fund, T60_nyq, K_hardening, hammer_mass, string_mass, output_scale, exc_x0, bridge_refl</li>
              <li><strong>Linearni interpolace</strong> pro exponenty a koeficienty:
                p_hardening, disp_coeff, detune_cents, gauge</li>
              <li><strong>Zaokrouhleni</strong> pro celociselne parametry: n_strings, n_disp_stages</li>
            </ul>
            <div className="bg-zinc-50 p-3 rounded border border-zinc-200 mt-2 font-mono text-[11px]">
              <p className="text-zinc-500 mb-1">Klicove rovnice (Chabassier et al.):</p>
              <p>f<sub>n</sub> = n &middot; F<sub>0</sub> &middot; &radic;(1 + B &middot; n&sup2;) &nbsp;&mdash;&nbsp; inharmonicita</p>
              <p>&sigma;<sub>j</sub> = b<sub>1</sub> + b<sub>3</sub> &middot; &omega;<sub>j</sub>&sup2; &nbsp;&mdash;&nbsp; frekvencne zavisly utlum</p>
              <p>B = &pi;&sup3; &middot; E &middot; d&#8308; / (64 &middot; T &middot; L&sup2;) &nbsp;&mdash;&nbsp; koeficient inharmonicity</p>
            </div>
          </section>

          <section>
            <h3 className="text-sm font-bold text-zinc-800 mb-2 flex items-center gap-2">
              <Waves className="w-4 h-4 text-blue-600" />
              Aditivni synteza
            </h3>
            <p className="mb-2">
              Rezim aditivni syntezy analyzuje harmonicke slozky zvuku - amplitudy a casove konstanty
              jednotlivych partialu. Kazda nota muze mit az 8 velocity vrstev (vel 0-7).
            </p>
            <p>
              Amplitudy partialu jsou interpolovany v <strong>logaritmickem prostoru (dB)</strong>,
              protoze perceptualni vyznam amplitudy je logaritmicky. Pro partialy s nulovou amplitudou
              se pouziva kubicky spline v linearnim prostoru.
            </p>
          </section>

          <section>
            <h3 className="text-sm font-bold text-zinc-800 mb-2 flex items-center gap-2">
              <GitBranch className="w-4 h-4 text-blue-600" />
              Interpolacni metoda: Monotonni kubicky spline
            </h3>
            <p className="mb-2">
              Program pouziva <strong>Fritsch-Carlsonuv monotonni kubicky Hermituv spline</strong> misto
              jednoduche linerani interpolace. Vyhody:
            </p>
            <ul className="list-disc list-inside space-y-1 ml-2">
              <li><strong>C1 spojitost</strong> - Hladka prvni derivace, zadne skoky v prubezich parametru</li>
              <li><strong>Monotonnost</strong> - Zadne prestreleni (overshooting) mezi datovymi body</li>
              <li><strong>Fyzikalni korektnost</strong> - Respektuje nelinearni prubehy parametru piana
                (napr. B neni linearni funkce MIDI cisla)</li>
            </ul>
            <p className="mt-2 text-zinc-500 text-xs">
              Pro 2 kotevni body se pouziva linearni fallback. Pro 3+ bodu se pocita plny kubicky spline.
            </p>
          </section>

          <section>
            <h3 className="text-sm font-bold text-zinc-800 mb-2 flex items-center gap-2">
              <Anchor className="w-4 h-4 text-blue-600" />
              Kotevni body (Anchors)
            </h3>
            <ul className="list-disc list-inside space-y-1 ml-2">
              <li><strong>Auto-Select</strong> - Automaticky vybere kotevni body na zaklade konzistence
                napric bankami (kazda oktava).</li>
              <li><strong>Manualni vyber</strong> - Zadejte MIDI cislo (napr. <code className="bg-zinc-100 px-1 rounded">55</code>)
                nebo MIDI/velocity (napr. <code className="bg-zinc-100 px-1 rounded">55/127</code>).
                Nota je vzdy vzata z <strong>aktivni banky</strong>.</li>
              <li><strong>Bez velocity</strong> - Pokud zadete jen MIDI cislo (napr. 55), pridaji se kotevni body
                pro vsechny dostupne velocity vrstvy dane noty v aktivni bance.</li>
              <li><strong>S velocity</strong> - Format <code className="bg-zinc-100 px-1 rounded">55/3</code> prida
                kotvu jen pro konkretni velocity vrstvu.</li>
              <li>Kotevni body slouzi jako referencni vzorky pro interpolaci modelu.</li>
              <li>Doporucuje se minimalne 2 kotevni body pro smysluplnou analyzu.</li>
              <li>U kazde kotvy je zobrazeno MIDI cislo, velocity a nazev banky.</li>
            </ul>

            <h4 className="text-xs font-bold text-zinc-700 mt-3 mb-1">Slucovani anchor bodu z vice bank</h4>
            <p>
              Anchor soubor muze obsahovat stejne noty (MIDI + velocity) z ruznych bank.
              Program to akceptuje - vysledne parametry anchor noty se pocitaji jako
              <strong> vazeny prumer</strong> vsech parametru. U aditivniho modu: pokud nektere noty maji
              vice partialu nez ostatni, chybejici partialy se doplnuji z not, ktere je maji.
            </p>
          </section>

          <section>
            <h3 className="text-sm font-bold text-zinc-800 mb-2 flex items-center gap-2">
              <Filter className="w-4 h-4 text-blue-600" />
              Prah odchylky a detekce anomalii
            </h3>
            <p className="mb-2">
              Prah urcuje maximalni povolenou odchylku vzorku od interpolovaneho modelu.
              Vzorky s vyssi odchylkou jsou oznaceny jako anomalie (cervene).
            </p>
            <h4 className="text-xs font-bold text-zinc-700 mt-2 mb-1">Vazena odchylka (physics-informed)</h4>
            <p className="mb-2">
              Metrika odchylky pouziva <strong>perceptualni vazeni</strong>: nizke partialy (zaklad, 2., 3. harmonicka)
              maji vyssi vahu nez vysoke partialy. Toto odpovida fyzice - vyssi partialy maji
              kratsi dobu dozneni (&sigma;<sub>j</sub> = b<sub>1</sub> + b<sub>3</sub>&middot;&omega;<sup>2</sup>)
              a jsou mene stabilni.
            </p>
            <div className="bg-zinc-50 p-3 rounded border border-zinc-200 font-mono text-[11px]">
              <p>w<sub>j</sub> = 1 / (1 + (j-1)/5)</p>
              <p className="text-zinc-400 mt-1">Partial 1: vaha 1.0 | Partial 6: vaha 0.5 | Partial 11: vaha 0.33</p>
            </div>

            <h4 className="text-xs font-bold text-zinc-700 mt-3 mb-1">Metody korekce</h4>
            <ul className="list-disc list-inside space-y-1 ml-2">
              <li><strong>Threshold</strong> - Pevny prah odchylky</li>
              <li><strong>Z-Score</strong> - Statisticka odchylka od prumeru</li>
              <li><strong>IQR</strong> - Interkvartilovy rozsah s horni i dolni mezi (spravny kvartilovy
                vypocet s linearni interpolaci)</li>
              <li><strong>Interpolate</strong> - Vsechny vzorky oznaceny jako dobre</li>
            </ul>
          </section>

          <section>
            <h3 className="text-sm font-bold text-zinc-800 mb-2 flex items-center gap-2">
              <Download className="w-4 h-4 text-blue-600" />
              Export
            </h3>
            <h4 className="text-xs font-bold text-zinc-700 mb-1">Export sloucene banky</h4>
            <p className="mb-2">
              Exportuje vyslednou sloucenou banku jako JSON. Pro fyzikalni mod pouziva klice
              <code className="bg-zinc-100 px-1 rounded">m021</code>, pro aditivni mod s velocity vrstvami
              <code className="bg-zinc-100 px-1 rounded">m021_vel0</code> az
              <code className="bg-zinc-100 px-1 rounded">m021_vel7</code>.
              Soubor se ulozi do slozky Stahnute soubory prohlizece.
            </p>

            <h4 className="text-xs font-bold text-zinc-700 mb-1 flex items-center gap-2">
              <Upload className="w-3 h-3" />
              Export a import kotevnich bodu
            </h4>
            <p className="mb-2">
              Kotevni body lze <strong>exportovat</strong> do souboru
              <code className="bg-zinc-100 px-1 rounded">anchor-export-YYMMDDhhmm.json</code>.
              Soubor obsahuje MIDI, velocity, ID banky a kompletni parametry kazde anchor noty.
            </p>
            <p className="mb-2">
              Ulozeny anchor soubor lze <strong>importovat</strong> zpet - importovane anchory se slouci
              s existujicimi (bez duplikatu). Toto umoznuje postupne budovat sadu kotevnich bodu
              napric vice sezenimi.
            </p>
            <div className="bg-amber-50 p-3 rounded border border-amber-200 text-amber-800 text-xs">
              <strong>Tip:</strong> Anchor soubor je mozne aktivne rozsirovat - nactete existujici anchory,
              pridejte nove a opet exportujte. Slucovani anchor bodu ze stejnych MIDI not z ruznych bank
              se provadi automaticky (vazeny prumer parametru).
            </div>
          </section>

          <section className="bg-blue-50 p-4 rounded-lg border border-blue-100">
            <h3 className="text-sm font-bold text-blue-800 mb-2">Doporuceny postup</h3>
            <ol className="list-decimal list-inside space-y-1 text-blue-700">
              <li>Vyberte jednu nebo vice bank z repozitare</li>
              <li>Oznacte aktivni banku (zeleny radio button) jako zdroj kotevnich bodu</li>
              <li>Pouzijte AUTO-SELECT nebo manualne pridejte kotevni body (MIDI nebo MIDI/velocity)</li>
              <li>Upravte prah odchylky podle potreby</li>
              <li>Prozkumejte anomalie kliknutim na body v grafu</li>
              <li>Pouzijte "Upravit dataset" pro odstraneni anomalii</li>
              <li>Exportujte kotevni body pro budouci pouziti</li>
              <li>Exportujte vyslednou sloucenou banku</li>
            </ol>
          </section>

          <section className="bg-zinc-50 p-4 rounded-lg border border-zinc-200">
            <h3 className="text-xs font-bold text-zinc-500 mb-2 uppercase tracking-wider">Vedecke reference</h3>
            <ul className="space-y-1 text-[11px] text-zinc-500">
              <li>Chabassier, J., Chaigne, A. &amp; Joly, P. (2013). <em>Time domain simulation of a piano.</em> ESAIM: M2AN.</li>
              <li>Simionato, R., Fasciani, S. &amp; Holm, S. (2024). <em>Physics-informed differentiable method for piano modeling.</em> Frontiers in Signal Processing.</li>
              <li>Bank, B. &amp; Valimaki, V. (2003). <em>Robust loss filter design for digital waveguide synthesis.</em> IEEE SPL.</li>
              <li>Chaigne, A. &amp; Askenfelt, A. (1994). <em>Numerical simulations of piano strings.</em> JASA.</li>
            </ul>
          </section>
        </CardContent>
      </Card>
    </div>
  );
};
