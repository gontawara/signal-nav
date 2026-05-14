"""
FastAPI サーバー。
ルート計算のAPIエンドポイントと静的ファイルの配信を行う。
"""

from contextlib import asynccontextmanager
from pathlib import Path

import networkx as nx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from signal_nav.routing import build_graph, find_route, DEFAULT_SPEED_MPS


# --- リクエスト/レスポンスのスキーマ ---

class RouteRequest(BaseModel):
    """ルート計算リクエスト"""
    origin_lat: float
    origin_lng: float
    dest_lat: float
    dest_lng: float


class RouteInfo(BaseModel):
    """1つのルートの情報"""
    coordinates: list[list[float]]  # [[lat, lng], ...]
    distance_m: float
    time_s: float
    signal_count: int


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
    """2つのルート（最短時間 / 信号最小）を計算して返す"""
    result = find_route(
        graph,
        origin=(req.origin_lat, req.origin_lng),
        destination=(req.dest_lat, req.dest_lng),
    )

    def to_route_info(route_data: dict) -> RouteInfo:
        coords = [
            [graph.nodes[n]["y"], graph.nodes[n]["x"]]
            for n in route_data["nodes"]
        ]
        return RouteInfo(
            coordinates=coords,
            distance_m=route_data["distance_m"],
            time_s=route_data["time_s"],
            signal_count=route_data["signal_count"],
        )

    return RouteResponse(
        shortest=to_route_info(result["shortest"]),
        min_signal=to_route_info(result["min_signal"]),
    )
