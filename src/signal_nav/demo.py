"""
動作確認用スクリプト。
学研都市駅 → 九州大学伊都キャンパスのルートを地図上に表示する。
"""

import folium

from signal_nav.routing import build_graph, find_route


def route_to_coords(G, nodes: list[int]) -> list[tuple[float, float]]:
    """ノードIDのリストを (緯度, 経度) のリストに変換する。"""
    return [(G.nodes[n]["y"], G.nodes[n]["x"]) for n in nodes]


def create_route_map(G, result: dict, origin: tuple, destination: tuple) -> folium.Map:
    """2つのルートを地図上に描画する。"""
    # 地図の中心を出発地と目的地の中点にする
    center_lat = (origin[0] + destination[0]) / 2
    center_lon = (origin[1] + destination[1]) / 2
    m = folium.Map(location=[center_lat, center_lon], zoom_start=13)

    # 最短時間ルート（青）
    shortest_coords = route_to_coords(G, result["shortest"]["nodes"])
    folium.PolyLine(
        shortest_coords,
        color="blue",
        weight=5,
        opacity=0.7,
        popup=(
            f"最短時間ルート<br>"
            f"距離: {result['shortest']['distance_m'] / 1000:.2f} km<br>"
            f"時間: {result['shortest']['time_s'] / 60:.1f} 分<br>"
            f"信号: {result['shortest']['signal_count']} 回"
        ),
    ).add_to(m)

    # 信号最小ルート（赤）
    min_signal_coords = route_to_coords(G, result["min_signal"]["nodes"])
    folium.PolyLine(
        min_signal_coords,
        color="red",
        weight=5,
        opacity=0.7,
        popup=(
            f"信号最小ルート<br>"
            f"距離: {result['min_signal']['distance_m'] / 1000:.2f} km<br>"
            f"時間: {result['min_signal']['time_s'] / 60:.1f} 分<br>"
            f"信号: {result['min_signal']['signal_count']} 回"
        ),
    ).add_to(m)

    # 出発地マーカー（緑）
    folium.Marker(
        location=origin,
        popup="出発: 学研都市駅",
        icon=folium.Icon(color="green", icon="play", prefix="fa"),
    ).add_to(m)

    # 目的地マーカー（赤）
    folium.Marker(
        location=destination,
        popup="目的地: 九州大学伊都キャンパス",
        icon=folium.Icon(color="red", icon="flag", prefix="fa"),
    ).add_to(m)

    # ルート上の信号機を表示（小さい黄色の円）
    for nodes_list in [result["shortest"]["nodes"], result["min_signal"]["nodes"]]:
        for n in nodes_list:
            if G.nodes[n]["has_signal"]:
                folium.CircleMarker(
                    location=[G.nodes[n]["y"], G.nodes[n]["x"]],
                    radius=3,
                    color="orange",
                    fill=True,
                    fill_opacity=0.8,
                ).add_to(m)

    # 凡例
    legend_html = """
    <div style="position: fixed; bottom: 30px; left: 30px; z-index: 1000;
                background: white; padding: 10px; border-radius: 5px;
                border: 2px solid grey; font-size: 14px;">
        <b>ルート比較</b><br>
        <span style="color: blue;">━━</span> 最短時間ルート<br>
        <span style="color: red;">━━</span> 信号最小ルート<br>
        <span style="color: orange;">●</span> 信号機
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    return m


def main():
    # 学研都市駅 (緯度, 経度)
    origin = (33.5792691, 130.2618238)
    # 九州大学伊都キャンパス (緯度, 経度)
    destination = (33.5964, 130.2175)

    print("道路ネットワークを取得中...")
    G = build_graph()

    signal_count = sum(1 for _, d in G.nodes(data=True) if d.get("has_signal"))
    print(f"グラフ構築完了: ノード {len(G.nodes)}, エッジ {len(G.edges)}, 信号 {signal_count}")

    print("\nルート計算中...")
    result = find_route(G, origin, destination)

    # コンソール出力
    print("\n=== 最短時間ルート ===")
    s = result["shortest"]
    print(f"  距離: {s['distance_m'] / 1000:.2f} km")
    print(f"  所要時間: {s['time_s'] / 60:.1f} 分")
    print(f"  信号通過数: {s['signal_count']} 回")

    print("\n=== 信号最小ルート (ペナルティ=60秒) ===")
    ms = result["min_signal"]
    print(f"  距離: {ms['distance_m'] / 1000:.2f} km")
    print(f"  所要時間: {ms['time_s'] / 60:.1f} 分")
    print(f"  信号通過数: {ms['signal_count']} 回")

    print("\n=== 比較 ===")
    dist_diff = ms["distance_m"] - s["distance_m"]
    signal_diff = s["signal_count"] - ms["signal_count"]
    print(f"  距離差: {dist_diff / 1000:+.2f} km")
    print(f"  信号削減: {signal_diff} 回")

    # 地図生成
    print("\n地図を生成中...")
    route_map = create_route_map(G, result, origin, destination)
    output_path = "data/route_comparison.html"
    route_map.save(output_path)
    print(f"地図を保存: {output_path}")
    print("→ ブラウザで開いてルートを確認してください")


if __name__ == "__main__":
    main()
