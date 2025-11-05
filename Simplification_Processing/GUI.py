# -*- coding: utf-8 -*-
# OSMnx + Conversão URN + mapas sincronizados (vetorial)

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
from shapely.strtree import STRtree
from concurrent.futures import ProcessPoolExecutor, as_completed
from shapely import wkb as _wkb
import warnings
import sys
import os
import re

import geopandas as gpd
import shapely
from PIL import Image, ImageTk
from shapely.geometry import LineString, MultiLineString, GeometryCollection
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

# --- IMPORTS DO REPOSITÓRIO (seu código) ---
from Graph_structure import Crossing_Checking, GraphBuilder

warnings.simplefilter("ignore")

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(__file__)
    return os.path.join(base_path, relative_path)

# --- helper para multiprocessing: precisa ser top-level no Windows ---
def _check_pair_wkb(i, j, wkb1, wkb2):
    g1 = _wkb.loads(wkb1)
    g2 = _wkb.loads(wkb2)
    if not g1.intersects(g2):
        return None
    inter = g1.intersection(g2)
    if inter.is_empty:
        return None
    pts = []
    if inter.geom_type == 'Point':
        pts = [(inter.x, inter.y)]
    elif inter.geom_type == 'MultiPoint':
        pts = [(p.x, p.y) for p in inter.geoms]
    # ignoramos sobreposições colineares (LineString/MultiLineString)
    return (i, j, pts) if pts else None


# ---------- util: redirecionar stdout/stderr para o Text (TEE com filtro) ----------
class TeeToText:
    def __init__(self, text_widget, orig_stream, filter_fn=None):
        self.text = text_widget
        self.orig = orig_stream
        self.filter_fn = filter_fn

    def write(self, s: str):
        try:
            if self.orig:
                self.orig.write(s)
        except Exception:
            pass

        out = s
        try:
            if self.filter_fn:
                out = self.filter_fn(s)
        except Exception:
            out = s

        if not out:
            return

        if self.text:
            try:
                self.text.after(0, self._append, out)
            except Exception:
                pass

    def _append(self, s: str):
        try:
            self.text.insert(tk.END, s)
            self.text.see(tk.END)
        except Exception:
            pass

    def flush(self):
        try:
            if self.orig:
                self.orig.flush()
        except Exception:
            pass


# ============== Janela / App ==============

class URNApp(tk.Tk):
    def __init__(self):
        super().__init__()

        # --- ÍCONE DA JANELA (test.ico / test.png) ---
        def _resource_path(rel_path: str) -> str:
            base = getattr(sys, "_MEIPASS", os.path.abspath("."))
            return os.path.abspath(os.path.join(base, rel_path))

        try:
            ico = _resource_path("test.ico")
            png = _resource_path("test.png")

            if sys.platform.startswith("win") and os.path.exists(ico):
                self.iconbitmap(ico)
            elif os.path.exists(png):
                self._icon_img = tk.PhotoImage(file=png)  # manter referência
                self.iconphoto(True, self._icon_img)
            else:
                print("[icon] aviso: test.ico/test.png não encontrados")
        except Exception as e:
            print(f"[icon] aviso: não consegui definir o ícone: {e}")
        # --- fim do bloco de ícone ---

        self.title("OSM → URN (SpatialRepresentationURN)")
        Logo = resource_path('test.png')
        try:
            ico = Image.open(Logo)
            photo = ImageTk.PhotoImage(ico)
            self.wm_iconphoto(False, photo)
        except Exception:
            pass
        self.geometry("1300x840")

        # Estado
        self.osm_place_gdf = None
        self.osm_edges_gdf = None
        self.current_gdf_edges = None   # <- FONTE ATIVA (OSM OU SHP)
        self.converted_lines = None
        self.sync_in_progress = False
        self.right_axes_active = False

        # Para arquivo local
        self.local_shp_path = tk.StringVar()
        self.output_name = tk.StringVar()

        # Busca OSM
        self.place_query = tk.StringVar()
        self.place_choices = []  # [(label, idx), ...]

        # Para exportar ORIGINAL
        self._original_gdf_3857 = None  # guardo a fonte em EPSG:3857

        # UI
        self._build_layout()

        # Redirecionar stdout/stderr com filtro (captura tqdm/porcentagem)
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        sys.stdout = TeeToText(self.log, self._orig_stdout, self._tee_filter)
        sys.stderr = TeeToText(self.log, self._orig_stderr, self._tee_filter)

        self._log("Pronto. Busque uma cidade, selecione um .shp ou converta.\n")

    # ---------- UI ----------
    def _build_layout(self):
        top = tk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.X, padx=10, pady=8)

        # Busca OSMnx
        tk.Label(top, text="Cidade/Região (OSMnx):").grid(row=0, column=0, sticky="e")
        tk.Entry(top, textvariable=self.place_query, width=40).grid(row=0, column=1, sticky="w", padx=4)
        tk.Button(top, text="Buscar", command=self._thread(self.search_places)).grid(row=0, column=2, padx=5)

        # Lista de resultados
        self.results_list = tk.Listbox(top, width=60, height=4)
        self.results_list.grid(row=1, column=0, columnspan=3, sticky="we", pady=4)
        self.results_list.bind("<<ListboxSelect>>", self._on_result_select)

        # Baixar/exibir
        tk.Button(top, text="Baixar & Mostrar OSM",
                  command=self._thread(self.download_and_show_osm),
                  bg="#2b7", fg="white").grid(row=0, column=3, padx=10)

        ttk.Separator(top, orient="vertical").grid(row=0, column=4, rowspan=2, sticky="ns", padx=10)

        # Arquivo local
        tk.Label(top, text="Arquivo .shp:").grid(row=0, column=5, sticky="e")
        tk.Entry(top, textvariable=self.local_shp_path, width=38).grid(row=0, column=6, sticky="w", padx=4)
        tk.Button(top, text="Selecionar", command=self.select_local_shp).grid(row=0, column=7, padx=5)

        # Saída
        tk.Label(top, text="Arquivo de saída (base):").grid(row=1, column=5, sticky="e")
        tk.Entry(top, textvariable=self.output_name, width=28).grid(row=1, column=6, sticky="w", padx=4)
        tk.Button(top, text="Salvar como…", command=self._choose_output_path).grid(row=1, column=7, padx=5)

        tk.Button(top, text="Converter",
                  command=self._thread(self.convert_current),
                  bg="#27f", fg="white").grid(row=1, column=8, padx=5)

        # Figura com 2 eixos
        fig_frame = tk.Frame(self)
        fig_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(6, 4))

        self.fig = plt.Figure(figsize=(12, 6), dpi=100, tight_layout=True)
        self.ax_left = self.fig.add_subplot(121)
        self.ax_right = self.fig.add_subplot(122)
        self.ax_right.set_visible(False)

        # Canvas/toolbar
        self.canvas = FigureCanvasTkAgg(self.fig, master=fig_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.toolbar = NavigationToolbar2Tk(self.canvas, fig_frame)
        self.toolbar.update()

        # Sem eixos
        self.ax_left.set_axis_off()
        self.ax_right.set_axis_off()

        # Eventos de pan/zoom
        self._panning = False
        self._pan_start = None
        self.canvas.mpl_connect('scroll_event', self._on_scroll_zoom)
        self.canvas.mpl_connect('button_press_event', self._on_mouse_press)
        self.canvas.mpl_connect('button_release_event', self._on_mouse_release)
        self.canvas.mpl_connect('motion_notify_event', self._on_mouse_move)

        # Sincronização entre eixos
        self.ax_left.callbacks.connect('xlim_changed', self._on_xlim_changed_left)
        self.ax_left.callbacks.connect('ylim_changed', self._on_ylim_changed_left)
        self.ax_right.callbacks.connect('xlim_changed', self._on_xlim_changed_right)
        self.ax_right.callbacks.connect('ylim_changed', self._on_ylim_changed_right)

        # Status + Log
        status_row = tk.Frame(self)
        status_row.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(2, 6))
        self.status_var = tk.StringVar(value="Pronto.")
        self.progress = ttk.Progressbar(status_row, length=520, mode='determinate')
        self.progress.pack(side=tk.LEFT)
        tk.Label(status_row, textvariable=self.status_var).pack(side=tk.LEFT, padx=8)

        log_frame = tk.Frame(self)
        log_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=False, padx=10, pady=(4, 8))
        self.log = tk.Text(log_frame, height=10, wrap="word")
        log_scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # borda/linha divisória entre os mapas
        self._draw_separator()

    # ---------- Utils ----------
    def _thread(self, fn):
        def runner():
            t = threading.Thread(target=fn, daemon=True)
            t.start()
        return runner

    def _log(self, msg):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", end="")  # cai no Tee (Text + terminal)

    def set_progress(self, v, text=None):
        self.progress["value"] = max(0, min(100, int(v)))
        if text is not None:
            self.status_var.set(text)
        self.update_idletasks()

    def _choose_output_path(self):
        p = filedialog.asksaveasfilename(
            title="Salvar como (base do nome)",
            defaultextension="",
            filetypes=[("Qualquer", "*.*")]
        )
        if p:
            self.output_name.set(p)
            self._log(f"Saída definida: {p}\n")

    def _active_axes(self, event):
        return event.inaxes if event.inaxes in (self.ax_left, self.ax_right) else None

    # ======= Zoom/Pan (scroll invertido, pan Y invertido) =======
    def _on_scroll_zoom(self, event):
        ax = self._active_axes(event)
        if ax is None:
            return
        scale = (1 / 1.2) if event.button == 'up' else 1.2  # invertido
        xlim = ax.get_xlim(); ylim = ax.get_ylim()
        xdata, ydata = event.xdata, event.ydata
        if xdata is None or ydata is None:
            return
        ax.set_xlim([xdata - (xdata - xlim[0]) * scale, xdata + (xlim[1] - xdata) * scale])
        ax.set_ylim([ydata - (ydata - ylim[0]) * scale, ydata + (ylim[1] - ydata) * scale])
        self.canvas.draw_idle()
        self._sync_limits(from_left=(ax is self.ax_left))
        self._draw_separator()

    def _on_mouse_press(self, event):
        if event.button != 2:
            return
        ax = self._active_axes(event)
        if ax is None:
            return
        self._panning = True
        self._pan_start = (ax, event.x, event.y, ax.get_xlim(), ax.get_ylim())

    def _on_mouse_release(self, event):
        if event.button != 2:
            return
        if not self._panning:
            return
        ax, *_ = self._pan_start
        self._panning = False
        self._pan_start = None
        self._sync_limits(from_left=(ax is self.ax_left))
        self._draw_separator()

    def _on_mouse_move(self, event):
        if not self._panning or self._pan_start is None:
            return
        ax, x0, y0, xlim0, ylim0 = self._pan_start
        if event.inaxes is not ax:
            return
        dx_pix = event.x - x0
        dy_pix = event.y - y0

        bbox = ax.get_window_extent().transformed(self.fig.dpi_scale_trans.inverted())
        width_px = bbox.width * self.fig.dpi
        height_px = bbox.height * self.fig.dpi

        dx_data = -dx_pix * (xlim0[1] - xlim0[0]) / width_px
        dy_data = -dy_pix * (ylim0[1] - ylim0[0]) / height_px  # invertido

        ax.set_xlim(xlim0[0] + dx_data, xlim0[1] + dx_data)
        ax.set_ylim(ylim0[0] + dy_data, ylim0[1] + dy_data)
        self.canvas.draw_idle()
        self._draw_separator()

    # ---------- filtro do tee: tqdm → barra de progresso, sem poluir log ----------
    def _tee_filter(self, s: str) -> str:
        out_lines = []
        for line in s.splitlines(True):
            m = re.search(r'^\s*(\d{1,3})%\|', line)
            if m:
                try:
                    perc = int(m.group(1))
                    self.set_progress(max(0, min(100, perc)), f"Conversão… {perc}%")
                except Exception:
                    pass
                continue
            m2 = re.search(r'(\d{1,3})\s*%', line)
            if m2 and '|' not in line:
                try:
                    perc = int(m2.group(1))
                    self.set_progress(max(0, min(100, perc)), f"Conversão… {perc}%")
                except Exception:
                    pass
                if re.fullmatch(r'\s*\d{1,3}\s*%\s*\r?\n?', line):
                    continue
            out_lines.append(line)
        return "".join(out_lines)

    # ---------- seleção/local ----------
    def select_local_shp(self):
        p = filedialog.askopenfilename(filetypes=[("Shapefiles", "*.shp")])
        if p:
            self.local_shp_path.set(p)
            self._log(f"Selecionado .shp: {p}\n")
            # carrega e mostra imediatamente
            self._thread(lambda: self.load_and_show_shp(p))()

    def load_and_show_shp(self, shp_path: str):
        try:
            self.set_progress(10, "Lendo SHP…")
            gdf = gpd.read_file(shp_path)
            if gdf.crs is None:
                gdf.set_crs(epsg=4326, inplace=True)
            gdf = gdf.to_crs(epsg=3857)

            # define um id caso não exista
            if 'full_id' not in gdf.columns:
                gdf = gdf.reset_index(drop=False)
                gdf['full_id'] = gdf['index'].astype(str)
                gdf.drop(columns=['index'], inplace=True)

            # mantém fonte ativa e original p/ export
            self.current_gdf_edges = gdf[['full_id', 'geometry']].copy()
            self._original_gdf_3857 = gdf[['geometry']].copy()

            self.set_progress(45, "Renderizando SHP…")

            # plota no painel esquerdo
            self.ax_left.clear()
            self.ax_left.set_axis_off()
            self.ax_left.set_title("SHP (edges)", fontsize=11, pad=6)
            try:
                gdf.plot(ax=self.ax_left, linewidth=0.6)
            except Exception as e:
                self._log(f"Erro ao plotar SHP: {e}\n")

            self.ax_left.set_aspect('equal', adjustable='datalim')
            self.canvas.draw()
            self._draw_separator()

            self.set_progress(0, "Pronto.")
            self._log("Mapa SHP exibido.\n")

            # opcional: esconder painel direito até converter
            self.ax_right.set_visible(False)
            self.right_axes_active = False
            self.canvas.draw_idle()
            self._draw_separator()

        except Exception as e:
            self._log(f"Erro ao carregar SHP: {e}\n")
            messagebox.showerror("Erro", f"Falha ao carregar SHP: {e}")
            self.set_progress(0, "Erro.")

    # ---------- OSMnx ----------
    def search_places(self):
        query = self.place_query.get().strip()
        if not query:
            messagebox.showwarning("Atenção", "Digite o nome da cidade/região.")
            return
        try:
            import osmnx as ox
        except Exception as e:
            messagebox.showerror("Erro", f"OSMnx não encontrado: {e}")
            return

        self._log(f"Buscando lugares para: {query}\n")
        self.set_progress(0, "Buscando lugares…")

        gdf = None
        last_err = None
        for call in (lambda: ox.geocode_to_gdf(query),
                     lambda: ox.geocoder.geocode_to_gdf(query)):
            try:
                gdf = call()
                break
            except Exception as e:
                last_err = e

        if gdf is None or gdf.empty:
            self._log(f"Falha na busca OSM: {last_err}\n")
            messagebox.showerror("Erro", f"Falha na busca OSM: {last_err}")
            self.set_progress(0, "Erro.")
            return

        try:
            gdf = gdf.to_crs(epsg=4326)
        except Exception:
            pass

        self.osm_place_gdf = gdf
        self.results_list.delete(0, tk.END)
        self.place_choices = []

        for idx, row in self.osm_place_gdf.iterrows():
            display = row.get('display_name') or row.get('name') or f"ID {idx}"
            admin = row.get('type') or ''
            self.place_choices.append((display, idx))
            self.results_list.insert(tk.END, f"{display}  [{admin}]")

        self._log(f"{len(self.place_choices)} resultado(s).\n")
        self.set_progress(0, "Pronto.")

    def _on_result_select(self, event):
        sel = self.results_list.curselection()
        if not sel:
            return
        i = sel[0]
        label, idx = self.place_choices[i]
        self._log(f"Selecionado: {label}\n")

    def download_and_show_osm(self):
        try:
            import osmnx as ox
        except Exception as e:
            messagebox.showerror("Erro", f"OSMnx não encontrado: {e}")
            return

        sel = self.results_list.curselection()
        if not sel:
            messagebox.showwarning("Atenção", "Selecione um resultado da lista.")
            return

        _, idx = self.place_choices[sel[0]]
        row = self.osm_place_gdf.loc[idx]
        geom = row.geometry

        self.set_progress(10, "Baixando OSM…")
        self._log("Baixando malha viária...\n")

        try:
            if geom.geom_type == "Point":
                G = ox.graph_from_point((geom.y, geom.x), dist=3000, network_type="drive")
            else:
                poly = geom
                if poly.geom_type == "MultiPolygon":
                    poly = shapely.unary_union([p for p in poly.geoms])
                G = ox.graph_from_polygon(poly, network_type="drive")
            nodes, edges = ox.graph_to_gdfs(G)
        except Exception as e:
            self._log(f"Erro ao baixar/gerar GDF: {e}\n")
            messagebox.showerror("Erro", f"Falha ao baixar OSM: {e}")
            self.set_progress(0, "Erro.")
            return

        try:
            edges = edges.to_crs(epsg=3857)
        except Exception:
            pass

        # MultiIndex -> id string único
        edges = edges.reset_index(drop=False)
        for cols in (["u", "v", "key"], ["u", "v"], []):
            if all(c in edges.columns for c in cols):
                edges["full_id"] = (
                    edges[cols].astype(str).agg("-".join, axis=1)
                    if cols else edges.index.astype(str)
                )
                break

        self.osm_edges_gdf = edges
        self.current_gdf_edges = edges[['full_id', 'geometry']].copy()  # <- fonte ativa
        self._original_gdf_3857 = edges[["geometry"]].copy()

        self.set_progress(45, "Renderizando OSM…")
        self._log(f"Edges carregados: {len(edges)}\n")

        # Plot vetorial
        self.ax_left.clear()
        self.ax_left.set_axis_off()
        self.ax_left.set_title("OSM (edges)", fontsize=11, pad=6)
        try:
            edges.plot(ax=self.ax_left, linewidth=0.6)
        except Exception as e:
            self._log(f"Erro ao plotar: {e}\n")

        self.ax_left.set_aspect('equal', adjustable='datalim')
        self.canvas.draw()
        self._draw_separator()

        self.set_progress(0, "Pronto.")
        self._log("Mapa OSM exibido.\n")

        # ocultar direito até converter
        self.ax_right.set_visible(False)
        self.right_axes_active = False
        self.canvas.draw_idle()
        self._draw_separator()

    # ---------- Conversão (repo) ----------
    def convert_current(self):
        savename_path = self.output_name.get().strip()
        if not savename_path:
            self._choose_output_path()
            savename_path = self.output_name.get().strip()
            if not savename_path:
                return

        self.set_progress(5, "Iniciando conversão…")
        self._log("Iniciando conversão…\n")

        # ========== Fonte de dados ==========
        source = None
        if self.osm_edges_gdf is not None and len(self.osm_edges_gdf) > 0:
            gdf_in = self.osm_edges_gdf[['full_id', 'geometry']].copy()
            self._original_gdf_3857 = self.osm_edges_gdf[['geometry']].copy()
            source = "OSMnx"
        elif self.local_shp_path.get():
            shp = self.local_shp_path.get()
            if not os.path.exists(shp):
                messagebox.showerror("Erro", "Arquivo .shp não encontrado.")
                return
            self._log(f"Lendo SHP local: {shp}\n")
            gdf_in = gpd.read_file(shp)
            if gdf_in.crs is None:
                gdf_in.set_crs(epsg=4326, inplace=True)
            gdf_in = gdf_in.to_crs(epsg=3857)
            if 'full_id' not in gdf_in.columns:
                gdf_in = gdf_in.reset_index(drop=False)
                gdf_in['full_id'] = gdf_in['index'].astype(str)
                gdf_in.drop(columns=['index'], inplace=True)
            self._original_gdf_3857 = gdf_in[['geometry']].copy()
            source = "SHP local"
        else:
            messagebox.showwarning("Atenção", "Baixe um OSM ou selecione um .shp.")
            return

        # ========== Preparação de geometrias ==========
        self._log(f"Preparando geometrias ({source})...\n")
        self.set_progress(12, "Preparando geometrias…")

        gdf = gdf_in[gdf_in['geometry'].notna()].copy()
        gdf = gdf.explode(index_parts=False, ignore_index=True)
        gdf['nodes'] = [[] for _ in range(len(gdf))]

        # dicionário compatível com etapas seguintes (Crossing_Checking)
        data = gdf.set_index('full_id').to_dict('index')
        geo_keys = list(data.keys())

        # ===== STRtree para reduzir pares =====
        geoms = [data[k]['geometry'] for k in geo_keys]
        valid_idx = [i for i, g in enumerate(geoms) if g is not None and not g.is_empty]
        geoms = [geoms[i] for i in valid_idx]
        keys_valid = [geo_keys[i] for i in valid_idx]

        if not geoms:
            messagebox.showerror("Erro", "Nenhuma geometria válida para converter.")
            return

        # índice espacial
        tree = STRtree(geoms)
        # log de versão em runtime
        self._log(f"Shapely {shapely.__version__} | GEOS {getattr(shapely, 'geos_version_string', 'n/a')}\n")

        # gerar pares candidatos com predicate="intersects"
        pairs = []
        try:
            # Shapely 2.x: query aceita sequência de geometrias e retorna (src_idx, dst_idx)
            src_idx, dst_idx = tree.query(geoms, predicate="intersects")
            pairs = [(int(i), int(j)) for i, j in zip(src_idx, dst_idx) if i < j]
        except TypeError:
            # Fallback para builds antigas: query(g) retorna geometrias
            wkb_to_idx = {g.wkb: i for i, g in enumerate(geoms)}
            seen = set()
            for i, g in enumerate(geoms):
                for gj in tree.query(g):  # bbox candidates
                    j = wkb_to_idx.get(gj.wkb)
                    if j is None or j == i:
                        continue
                    a, b = (i, j) if i < j else (j, i)
                    if (a, b) not in seen:
                        seen.add((a, b))
            pairs = list(seen)

        total_pairs = len(pairs)
        self._log(f"Verificando interseções (candidatos: {total_pairs})…\n")

        # ===== multiprocessamento por pares =====
        geoms_wkb = [gg.wkb for gg in geoms]

        # acumuladores por índice local (0..len(geoms)-1)
        nodes_acc = [[] for _ in geoms]

        checked = 0
        max_workers = os.cpu_count() or 2
        chunk_size = 5000  # ajuste se quiser

        try:
            with ProcessPoolExecutor(max_workers=max_workers) as ex:
                for start in range(0, total_pairs, chunk_size):
                    end = min(start + chunk_size, total_pairs)
                    batch = pairs[start:end]
                    futures = [
                        ex.submit(_check_pair_wkb, i, j, geoms_wkb[i], geoms_wkb[j])
                        for (i, j) in batch
                    ]
                    for fut in as_completed(futures):
                        res = fut.result()
                        checked += 1
                        if res:
                            i, j, pts = res
                            if pts:
                                # reconstrói Point apenas na thread principal
                                for x, y in pts:
                                    p = shapely.geometry.Point(x, y)
                                    nodes_acc[i].append(p)
                                    nodes_acc[j].append(p)
                        if total_pairs:
                            pct = min(92, int(100 * checked / total_pairs))
                            self.set_progress(pct, f"Interseções… {pct}%")
        except Exception as e:
            self._log(f"Erro nas interseções: {e}\n")
            messagebox.showerror("Erro", f"Falha ao checar interseções: {e}")
            self.set_progress(0, "Erro.")
            return

        # grava de volta nos dicionários originais (usando as chaves reais)
        for local_idx, key in enumerate(keys_valid):
            data[key]['nodes'].extend(nodes_acc[local_idx])

        # ========== Crossing_Checking + GraphBuilder ==========
        try:
            self._log("Calculando links de separação (Crossing_Checking)…\n")
            self.set_progress(94, "Calculando links…")
            separatLi, _ = Crossing_Checking(data, geo_keys)
            linkstg = list(separatLi)
            self.converted_lines = linkstg

            # salvar estrutura URN do repositório no diretório/base escolhidos
            out_dir, out_base = os.path.split(savename_path)
            self.set_progress(97, "Construindo e salvando URN…")
            if out_dir:
                prev = os.getcwd()
                try:
                    os.makedirs(out_dir, exist_ok=True)
                    os.chdir(out_dir)
                    self._log(f"Salvando em: {out_dir} (base: {out_base})\n")
                    urn_graph = GraphBuilder(linkstg, dict(), dict(), [], out_base)
                    urn_graph.run()
                    urn_graph.save()
                finally:
                    os.chdir(prev)
            else:
                self._log(f"Salvando (base: {out_base})\n")
                urn_graph = GraphBuilder(linkstg, dict(), dict(), [], out_base)
                urn_graph.run()
                urn_graph.save()

            # ====== exporta GeoJSON “URN” (linhas) ======
            try:
                out_geojson = savename_path if savename_path.lower().endswith(".geojson") \
                            else savename_path + ".geojson"
                geoms_out = self._flatten_lines_all(linkstg)
                ids = list(range(len(geoms_out)))
                gdf_links = gpd.GeoDataFrame({"id": ids}, geometry=geoms_out, crs="EPSG:3857")
                gdf_links = gdf_links.to_crs(epsg=4326)
                gdf_links.to_file(out_geojson, driver="GeoJSON")
                self._log(f"GeoJSON salvo: {out_geojson}\n")
            except Exception as e:
                self._log(f"Falha ao salvar GeoJSON extra: {e}\n")

            # ====== exporta ORIGINAL (sufixo _ORIGINAL.geojson) ======
            try:
                base_no_ext = savename_path[:-8] if savename_path.lower().endswith(".geojson") else savename_path
                out_original = f"{base_no_ext}_ORIGINAL.geojson"
                if self._original_gdf_3857 is not None and not self._original_gdf_3857.empty:
                    gdf_orig = self._original_gdf_3857.copy()
                    if gdf_orig.crs is None:
                        gdf_orig.set_crs(epsg=3857, inplace=True)
                    gdf_orig = gdf_orig.to_crs(epsg=4326)
                    gdf_orig.to_file(out_original, driver="GeoJSON")
                    self._log(f"ORIGINAL salvo: {out_original}\n")
                else:
                    self._log("ORIGINAL não disponível para exportação.\n")
            except Exception as e:
                self._log(f"Falha ao salvar ORIGINAL: {e}\n")

            self._log("URN salvo com sucesso.\n")
            self.set_progress(100, "Conversão concluída.")
        except Exception as e:
            self._log(f"Erro na conversão: {e}\n")
            messagebox.showerror("Erro", f"Falha na conversão: {e}")
            self.set_progress(0, "Erro.")
            return

        # ========== exibe no painel direito ==========
        self._show_converted_right()
        self.after(400, lambda: self.set_progress(0, "Pronto."))

    # --------- util: achatar geometrias em LineString ---------
    def _flatten_lines_all(self, geoms_iterable):
        out = []
        for g in geoms_iterable:
            out.extend(self._flatten_one(g))
        return out

    def _flatten_one(self, g):
        if isinstance(g, LineString):
            return [g]
        if isinstance(g, MultiLineString):
            res = []
            for gg in g.geoms:
                res.extend(self._flatten_one(gg))
            return res
        if isinstance(g, GeometryCollection):
            res = []
            for gg in g.geoms:
                res.extend(self._flatten_one(gg))
            return res
        return []

    def _show_converted_right(self):
        if not self.converted_lines:
            messagebox.showwarning("Atenção", "Nada convertido para exibir.")
            return

        self.ax_right.clear()
        self.ax_right.set_visible(True)
        self.ax_right.set_axis_off()
        self.ax_right.set_title("URN (convertido)", fontsize=11, pad=6)

        try:
            for ln in self._flatten_lines_all(self.converted_lines):
                xs, ys = ln.xy
                self.ax_right.plot(xs, ys, linewidth=0.8)
        except Exception as e:
            self._log(f"Erro ao plotar URN: {e}\n")

        self.ax_right.set_aspect('equal', adjustable='datalim')
        self.canvas.draw()
        self._draw_separator()

        self.right_axes_active = True
        self._log("Pan/zoom sincronizados entre os painéis.\n")
        self._sync_limits(from_left=True)

    # ---------- linha divisória entre os mapas ----------
    def _draw_separator(self):
        new_artists = []
        for a in self.fig.artists:
            if isinstance(a, Line2D) and a.get_transform() == self.fig.transFigure:
                continue
            new_artists.append(a)
        self.fig.artists = new_artists

        line = Line2D([0.5, 0.5], [0.06, 0.98], transform=self.fig.transFigure,
                      linewidth=1.0, color='0.65', alpha=0.9)
        self.fig.add_artist(line)
        self.canvas.draw_idle()

    # ---------- Sincronização de pan/zoom ----------
    def _sync_limits(self, from_left=True):
        if not self.right_axes_active or self.sync_in_progress:
            return
        self.sync_in_progress = True
        try:
            if from_left:
                x0, x1 = self.ax_left.get_xlim()
                y0, y1 = self.ax_left.get_ylim()
                self.ax_right.set_xlim(x0, x1)
                self.ax_right.set_ylim(y0, y1)
            else:
                x0, x1 = self.ax_right.get_xlim()
                y0, y1 = self.ax_right.get_ylim()
                self.ax_left.set_xlim(x0, x1)
                self.ax_left.set_ylim(y0, y1)
        finally:
            self.sync_in_progress = False
        self.canvas.draw_idle()
        self._draw_separator()

    def _on_xlim_changed_left(self, ax):
        if self.right_axes_active:
            self._sync_limits(from_left=True)

    def _on_ylim_changed_left(self, ax):
        if self.right_axes_active:
            self._sync_limits(from_left=True)

    def _on_xlim_changed_right(self, ax):
        if self.right_axes_active:
            self._sync_limits(from_left=False)

    def _on_ylim_changed_right(self, ax):
        if self.right_axes_active:
            self._sync_limits(from_left=False)


if __name__ == "__main__":
    app = URNApp()
    app.mainloop()
