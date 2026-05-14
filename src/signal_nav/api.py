"""
FastAPI サーバー。
ルート計算のAPIエンドポイントと静的ファイルの配信を行う。
"""

import math
from contextlib import asynccontextmanager
from pathlib import Path

import networkx as nx
import osmnx as ox
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

from signal_nav.routing import build_graph, find_route, DEFAULT_SPEED_MPS


# --- 定数 ---
# グラフの端から何メートル以上離れた座標を「範囲外」とみなすか
MAX_SNAP_DISTANCE_M = 1000


# --- リクエスト/レスポンスのスキーマ ---

class RouteRequest(BaseModel):
    """ルート計算リクエスト"""
    origin_lat: float
    origin_lng: float
    dest_lat: float
    dest_lng: float

    @field_validator("origin_lat", "dest_lat")
    @classmethod
    def lat_in_range(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError("緯度は -90〜90 の範囲で指定してください")
        return v

    @field_validator("origin_lng", "dest_lng")
    @classmethod
    def lng_in_range(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError("経度は -180〜180 の範囲で指定してください")
        return v


class RouteInfo(BaseModel):
    """1つのルートの情報"""
    coordinates: list[list[float]]  # [[lat, lng], ...]
    distance_m: float
    time_s: float
    signal_count: int
    signal_positions: list[list[float]]  # 信号機の座標 [[lat, lng], ...]


class RouteResponse(BaseModel):
    """ルート計算レスポンス"""
    shortest: RouteInfo
    min_signal: RouteInfo


# --- グラフの事前読み込み ---

graph: nx.MultiDiGraph | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """サーバー起動時にグラフを読み込む。
    リクエストのたびに読み込むと数秒かかるため、起動時に1回だけ行う。
    """
    global graph
    print("グラフを読み込み中...")
    graph = build_graph()
    signal_count = sum(1 for _, d in graph.nodes(data=True) if d.get("has_signal"))
    print(f"グラフ構築完了: ノード {len(graph.nodes)}, エッジ {len(graph.edges)}, 信号 {signal_count}")
    yield
    graph = None


# --- FastAPIアプリ ---

app = FastAPI(title="Signal Nav API", lifespan=lifespan)

# 静的ファイルの配信
STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    """トップページ（地図UI）を返す"""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.post("/api/route", response_model=RouteResponse)
async def calculate_route(req: RouteRequest):
    """2つのルート（最短時間 / 信号最小）を計算して返す。

    エラー時のHTTPステータス:
    - 422: 緯度経度の値が不正（Pydanticバリデーション）
    - 400: 指定座標が対象エリアから離れすぎている
    - 404: 2地点間のルートが見つからない（到達不可能）
    """
    # 座標がグラフの範囲内かチェック
    _validate_coordinates(req.origin_lat, req.origin_lng, "出発地")
    _validate_coordinates(req.dest_lat, req.dest_lng, "目的地")

    try:
        result = find_route(
            graph,
            origin=(req.origin_lat, req.origin_lng),
            destination=(req.dest_lat, req.dest_lng),
        )
    except nx.NetworkXNoPath:
        raise HTTPException(
            status_code=404,
            detail="2地点間のルートが見つかりません。道路でつながっていない可能性があります。",
        )

    def to_route_info(route_data: dict) -> RouteInfo:
        # エッジのgeometry属性があれば道路形状を使い、なければノード間直線
        coords: list[list[float]] = []
        nodes = route_data["nodes"]
        for i in range(len(nodes) - 1):
            u, v = nodes[i], nodes[i + 1]
            # MultiDiGraphなので最短エッジを選ぶ
            edge_data = min(
                graph[u][v].values(), key=lambda d: d["length"]
            )
            if "geometry" in edge_data:
                # geometry は shapely LineString。座標を展開する
                line_coords = list(edge_data["geometry"].coords)
                # geometryは (lng, lat) 順なので [lat, lng] に変換
                for j, (lng, lat) in enumerate(line_coords):
                    # 最初のエッジ以外は始点が前のエッジの終点と重複するので除く
                    if i > 0 and j == 0:
                        continue
                    coords.append([lat, lng])
            else:
                # geometry がないエッジはノード座標で直線
                if i == 0:
                    coords.append([graph.nodes[u]["y"], graph.nodes[u]["x"]])
                coords.append([graph.nodes[v]["y"], graph.nodes[v]["x"]])
        # ルート上の信号機の座標を抽出（グループ単位で重複除去）
        seen_groups: set[int] = set()
        signal_positions: list[list[float]] = []
        for n in route_data["nodes"]:
            nd = graph.nodes[n]
            if nd["has_signal"]:
                gid = nd["signal_group"]
                if gid not in seen_groups:
                    seen_groups.add(gid)
                    signal_positions.append([nd["y"], nd["x"]])

        return RouteInfo(
            coordinates=coords,
            distance_m=route_data["distance_m"],
            time_s=route_data["time_s"],
            signal_count=route_data["signal_count"],
            signal_positions=signal_positions,
        )

    return RouteResponse(
        shortest=to_route_info(result["shortest"]),
        min_signal=to_route_info(result["min_signal"]),
    )


def _validate_coordinates(lat: float, lng: float, label: str) -> None:
    """座標がグラフの対象エリア内かを検証する。

    ox.nearest_nodesは範囲外でも最も近いノードを返すだけでエラーにならない。
    そのため、スナップ先ノードとの距離を計算し、遠すぎる場合はエラーにする。
    """
    node = ox.nearest_nodes(graph, X=lng, Y=lat)
    node_lat = graph.nodes[node]["y"]
    node_lng = graph.nodes[node]["x"]

    # 簡易距離計算（Haversineの近似。近距離ならこれで十分）
    dlat = math.radians(lat - node_lat)
    dlng = math.radians(lng - node_lng)
    cos_lat = math.cos(math.radians(lat))
    distance_m = math.sqrt((dlat * 110540) ** 2 + (dlng * 111320 * cos_lat) ** 2)

    if distance_m > MAX_SNAP_DISTANCE_M:
        raise HTTPException(
            status_code=400,
            detail=f"{label}が対象エリアから離れすぎています（最寄りの道路まで {distance_m:.0f}m）。福岡市内の地点を指定してください。",
        )
