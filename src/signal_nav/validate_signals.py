"""
Day 1: 福岡市のOSM信号機データの網羅性を検証するスクリプト。

やること:
1. OSMnxで福岡市の道路ネットワークを取得
2. highway=traffic_signals ノードを抽出
3. 信号機の数・分布を可視化して、データが使えるか判断する

実行: uv run python src/validate_signals.py
"""

import osmnx as ox
import folium


def fetch_road_network(place: str = "Fukuoka, Japan"):
    """道路ネットワークをOSMから取得"""
    print(f"道路ネットワークを取得中: {place}")
    G = ox.graph_from_place(place, network_type="drive")
    print(f"  ノード数: {len(G.nodes)}")
    print(f"  エッジ数: {len(G.edges)}")
    return G


def extract_traffic_signals(G):
    """グラフから信号機ノードを抽出"""
    signal_nodes = []
    for node, data in G.nodes(data=True):
        highway = data.get("highway", "")
        # highway属性が文字列の場合とリストの場合がある
        if isinstance(highway, str):
            if highway == "traffic_signals":
                signal_nodes.append((node, data))
        elif isinstance(highway, list):
            if "traffic_signals" in highway:
                signal_nodes.append((node, data))
    return signal_nodes


def create_signal_map(G, signal_nodes, output_path: str = "data/signal_map.html"):
    """信号機の分布を地図上にプロットする"""
    # グラフの中心座標を取得
    center_lat = sum(d["y"] for _, d in G.nodes(data=True)) / len(G.nodes)
    center_lon = sum(d["x"] for _, d in G.nodes(data=True)) / len(G.nodes)

    m = folium.Map(location=[center_lat, center_lon], zoom_start=13)

    for node, data in signal_nodes:
        folium.CircleMarker(
            location=[data["y"], data["x"]],
            radius=3,
            color="red",
            fill=True,
            fill_opacity=0.7,
            popup=f"Node: {node}",
        ).add_to(m)

    m.save(output_path)
    print(f"地図を保存: {output_path}")
    return m


def main():
    # 1. 道路ネットワーク取得
    G = fetch_road_network()

    # 2. 信号機ノード抽出
    signal_nodes = extract_traffic_signals(G)
    print(f"\n信号機ノード数: {len(signal_nodes)}")

    total_nodes = len(G.nodes)
    signal_ratio = len(signal_nodes) / total_nodes * 100
    print(f"全ノードに対する割合: {signal_ratio:.2f}%")

    # 3. 可視化
    if signal_nodes:
        create_signal_map(G, signal_nodes)
        print("\n→ data/signal_map.html をブラウザで開いて分布を確認してください")
    else:
        print("\n⚠ 信号機ノードが0件。データが不十分な可能性が高い。")
        print("  対処案:")
        print("  (a) エリアを博多駅周辺に絞る")
        print("  (b) 信号ではなく交差点数の最小化に切り替える")
        print("  (c) 国土数値情報の信号機データを併用する")

    # 4. 判断材料を出力
    print("\n--- 判断材料 ---")
    print(f"道路ノード数: {total_nodes}")
    print(f"信号機ノード数: {len(signal_nodes)}")
    print(f"信号/ノード比: {signal_ratio:.2f}%")
    print("→ 信号機が100件未満なら、このデータだけでは厳しい可能性が高い")


if __name__ == "__main__":
    main()
