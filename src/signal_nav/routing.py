"""
ルーティングモジュール。
信号ペナルティを考慮した最短経路探索を提供する。

設計方針:
- エッジの重みを「所要時間（秒）」に統一する
- 信号機のあるノードに到達するエッジには、追加のペナルティ（秒）を加算する
- 重みはエッジ属性として事前計算し、nx.shortest_pathには属性名を渡す
  （コールバック関数方式はMultiDiGraphで挙動が不安定なため採用しない）
- 同じグラフ上で「信号ペナルティなし（最短時間）」と「信号ペナルティあり（信号回避）」の
  2種類のルートを計算し、比較できるようにする
"""

from pathlib import Path

import networkx as nx
import numpy as np
import osmnx as ox
from scipy.spatial import KDTree


# --- 定数 ---
DEFAULT_SPEED_MPS = 50 * 1000 / 3600  # 市街地の平均速度(m/s)。50km/hを仮定
DEFAULT_SIGNAL_PENALTY = 60  # 信号1回あたりのペナルティ(秒)
SIGNAL_GROUP_RADIUS_M = 30  # この距離(m)以内の信号ノードは同一交差点とみなす
GRAPH_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def build_graph(
    place: str = "Fukuoka, Japan",
    speed_mps: float = DEFAULT_SPEED_MPS,
    signal_penalty: float = DEFAULT_SIGNAL_PENALTY,
    use_cache: bool = True,
) -> nx.MultiDiGraph:
    """道路ネットワークを取得し、信号フラグとエッジの重みを付与して返す。

    初回はOSMからダウンロードし、GraphML形式でdata/にキャッシュする。
    2回目以降はキャッシュから読み込むため高速（数十秒→数秒）。

    各エッジに以下の属性を追加する:
    - travel_time: 所要時間（秒）。距離 / 平均速度
    - travel_time_with_penalty: 信号ペナルティを加算した所要時間（秒）

    Args:
        place: OSMnxに渡す地名文字列
        speed_mps: 平均速度(m/s)
        signal_penalty: 信号1回あたりのペナルティ(秒)
        use_cache: キャッシュを使うかどうか。Falseで強制再取得

    Returns:
        各ノードに "has_signal" (bool)、
        各エッジに "travel_time" と "travel_time_with_penalty" が付与されたグラフ
    """
    # --- キャッシュからの読み込み ---
    # placeをファイル名に使えるよう、スペースやカンマを置換
    safe_name = place.lower().replace(" ", "_").replace(",", "")
    cache_path = GRAPH_CACHE_DIR / f"{safe_name}.graphml"

    if use_cache and cache_path.exists():
        print(f"キャッシュから読み込み: {cache_path}")
        G = ox.load_graphml(cache_path)
    else:
        print(f"OSMからダウンロード: {place}")
        G = ox.graph_from_place(place, network_type="drive")
        # キャッシュとして保存
        GRAPH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        ox.save_graphml(G, cache_path)
        print(f"キャッシュに保存: {cache_path}")

    # ノードに信号フラグを付与
    for node, data in G.nodes(data=True):
        highway = data.get("highway", "")
        if isinstance(highway, str):
            data["has_signal"] = highway == "traffic_signals"
        elif isinstance(highway, list):
            data["has_signal"] = "traffic_signals" in highway
        else:
            data["has_signal"] = False

    # 近接する信号ノードを同一交差点としてグループ化する。
    # 中央分離帯のある道路では、上り・下り車線に別々の信号ノードが存在し、
    # 1つの交差点を通過しただけで信号2回カウントになる問題を防ぐ。
    _assign_signal_groups(G)

    # エッジに重みを事前計算して付与
    for u, v, data in G.edges(data=True):
        distance_m = data["length"]
        travel_time = distance_m / speed_mps

        data["travel_time"] = travel_time

        v_signal = G.nodes[v]["has_signal"]
        u_signal = G.nodes[u]["has_signal"]

        if v_signal and u_signal and G.nodes[u]["signal_group"] == G.nodes[v]["signal_group"]:
            # 同一交差点グループ内の移動にはペナルティを加算しない
            data["travel_time_with_penalty"] = travel_time
        elif v_signal:
            data["travel_time_with_penalty"] = travel_time + signal_penalty
        else:
            data["travel_time_with_penalty"] = travel_time

    return G


def find_route(
    G: nx.MultiDiGraph,
    origin: tuple[float, float],
    destination: tuple[float, float],
    speed_mps: float = DEFAULT_SPEED_MPS,
) -> dict:
    """2つのルート（最短時間 / 信号最小）を計算して返す。

    Args:
        G: build_graphで構築済みのグラフ
        origin: 出発地点の (緯度, 経度)
        destination: 目的地の (緯度, 経度)
        speed_mps: 平均速度(m/s)

    Returns:
        {
            "shortest": {
                "nodes": [ノードIDのリスト],
                "distance_m": 総距離(m),
                "time_s": 所要時間(秒),
                "signal_count": 信号通過数,
            },
            "min_signal": {
                "nodes": [ノードIDのリスト],
                "distance_m": 総距離(m),
                "time_s": 所要時間(秒),
                "signal_count": 信号通過数,
            },
        }
    """
    # 1. 座標をグラフ上の最近傍ノードに変換する
    orig_node = ox.nearest_nodes(G, X=origin[1], Y=origin[0])
    dest_node = ox.nearest_nodes(G, X=destination[1], Y=destination[0])

    # 2. 最短時間ルート（信号ペナルティなし）
    shortest_nodes = nx.shortest_path(
        G, orig_node, dest_node, weight="travel_time"
    )

    # 3. 信号最小ルート（信号ペナルティあり）
    min_signal_nodes = nx.shortest_path(
        G, orig_node, dest_node, weight="travel_time_with_penalty"
    )

    # 4. 各ルートの統計情報を集計する
    return {
        "shortest": _summarize_route(G, shortest_nodes, speed_mps),
        "min_signal": _summarize_route(G, min_signal_nodes, speed_mps),
    }


def _summarize_route(
    G: nx.MultiDiGraph,
    nodes: list[int],
    speed_mps: float,
    signal_wait: float = DEFAULT_SIGNAL_PENALTY,
) -> dict:
    """ルートの統計情報をまとめる。

    所要時間は「走行時間＋信号待ち時間」の合計。
    走行時間 = 距離 / 平均速度、信号待ち時間 = 信号数 × 1回あたりの待ち時間。

    Args:
        G: 道路ネットワークグラフ
        nodes: ルートのノードIDリスト
        speed_mps: 平均速度(m/s)
        signal_wait: 信号1回あたりの待ち時間(秒)

    Returns:
        ルート情報の辞書
    """
    distance = _route_distance(G, nodes)
    signal_count = _count_signals(G, nodes)
    drive_time = distance / speed_mps
    return {
        "nodes": nodes,
        "distance_m": distance,
        "time_s": drive_time + signal_count * signal_wait,
        "signal_count": signal_count,
    }


def _count_signals(G: nx.MultiDiGraph, nodes: list[int]) -> int:
    """ルート上の信号交差点の数を数える。

    近接する信号ノード（中央分離帯の上り・下りなど）は同一交差点として
    1回だけカウントする。グループIDはbuild_graph時に_assign_signal_groupsで付与済み。

    Args:
        G: 道路ネットワークグラフ
        nodes: ルートのノードIDリスト

    Returns:
        通過する信号交差点の数（ユニークなグループ数）
    """
    seen_groups: set[int] = set()
    for n in nodes:
        nd = G.nodes[n]
        if nd["has_signal"]:
            seen_groups.add(nd["signal_group"])
    return len(seen_groups)


def _assign_signal_groups(G: nx.MultiDiGraph) -> None:
    """信号ノードを近接距離でグループ化し、各ノードにsignal_group属性を付与する。

    KD木で半径SIGNAL_GROUP_RADIUS_M以内の信号ノード同士を検出し、
    Union-Findでグループにまとめる。非信号ノードにはsignal_group=-1を設定する。

    緯度経度→メートル変換は簡易式（福岡市付近で十分な精度）を使用。
    """
    # 信号ノードを収集
    signal_nodes = [n for n, d in G.nodes(data=True) if d.get("has_signal")]

    if not signal_nodes:
        for n in G.nodes:
            G.nodes[n]["signal_group"] = -1
        return

    # 緯度経度をメートル単位の近似座標に変換（福岡市付近: 緯度33.5度）
    # 経度1度 ≈ 111320 * cos(lat) メートル、緯度1度 ≈ 110540 メートル
    ref_lat_rad = np.radians(33.5)
    coords = np.array([
        [G.nodes[n]["y"] * 110540, G.nodes[n]["x"] * 111320 * np.cos(ref_lat_rad)]
        for n in signal_nodes
    ])

    # KD木で近傍探索
    tree = KDTree(coords)
    pairs = tree.query_pairs(r=SIGNAL_GROUP_RADIUS_M)

    # Union-Find でグループ化
    parent = list(range(len(signal_nodes)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i, j in pairs:
        union(i, j)

    # 各信号ノードにグループIDを付与
    for idx, n in enumerate(signal_nodes):
        G.nodes[n]["signal_group"] = find(idx)

    # 非信号ノードには-1を設定
    signal_set = set(signal_nodes)
    for n in G.nodes:
        if n not in signal_set:
            G.nodes[n]["signal_group"] = -1


def _route_distance(G: nx.MultiDiGraph, nodes: list[int]) -> float:
    """ルートの総距離(m)を計算する。

    Args:
        G: 道路ネットワークグラフ
        nodes: ルートのノードIDリスト

    Returns:
        総距離(m)
    """
    total = 0.0
    for i in range(len(nodes) - 1):
        # MultiDiGraphでは同じノードペア間に複数エッジがありうる
        # 最短のものを選ぶ
        edges = G[nodes[i]][nodes[i + 1]]
        total += min(d["length"] for d in edges.values())
    return total
