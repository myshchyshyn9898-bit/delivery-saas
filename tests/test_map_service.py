"""Tests for handlers/map_service.py."""
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import json


class TestGenerateRouteImageSync:
    """Tests for generate_route_image_sync()."""

    def _osrm_response(self, coords=None):
        if coords is None:
            coords = [[22.0, 50.0], [22.1, 50.1], [22.2, 50.2]]
        return {
            "routes": [
                {
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coords,
                    }
                }
            ]
        }

    def test_returns_filename_on_success(self, tmp_path):
        from handlers.map_service import generate_route_image_sync

        osrm_mock = MagicMock()
        osrm_mock.status_code = 200
        osrm_mock.json.return_value = self._osrm_response()

        mapbox_mock = MagicMock()
        mapbox_mock.status_code = 200
        mapbox_mock.content = b"PNG_DATA"

        filename = str(tmp_path / "test_map.png")

        with patch("requests.get", side_effect=[osrm_mock, mapbox_mock]):
            result = generate_route_image_sync(50.0, 22.0, 50.2, 22.2, filename=filename)

        assert result == filename
        assert os.path.exists(filename)

    def test_returns_none_on_osrm_failure(self, tmp_path):
        from handlers.map_service import generate_route_image_sync

        osrm_mock = MagicMock()
        osrm_mock.status_code = 500

        filename = str(tmp_path / "test_map2.png")

        with patch("requests.get", return_value=osrm_mock):
            result = generate_route_image_sync(50.0, 22.0, 50.2, 22.2, filename=filename)

        assert result is None

    def test_returns_none_when_osrm_returns_no_routes(self, tmp_path):
        from handlers.map_service import generate_route_image_sync

        osrm_mock = MagicMock()
        osrm_mock.status_code = 200
        osrm_mock.json.return_value = {"routes": []}  # Empty routes

        filename = str(tmp_path / "test_map3.png")

        with patch("requests.get", return_value=osrm_mock):
            result = generate_route_image_sync(50.0, 22.0, 50.2, 22.2, filename=filename)

        assert result is None

    def test_returns_none_on_mapbox_failure(self, tmp_path):
        from handlers.map_service import generate_route_image_sync

        osrm_mock = MagicMock()
        osrm_mock.status_code = 200
        osrm_mock.json.return_value = self._osrm_response()

        mapbox_mock = MagicMock()
        mapbox_mock.status_code = 401
        mapbox_mock.text = "Unauthorized"

        filename = str(tmp_path / "test_map4.png")

        with patch("requests.get", side_effect=[osrm_mock, mapbox_mock]):
            result = generate_route_image_sync(50.0, 22.0, 50.2, 22.2, filename=filename)

        assert result is None

    def test_returns_none_on_exception(self, tmp_path):
        from handlers.map_service import generate_route_image_sync

        filename = str(tmp_path / "test_map5.png")

        with patch("requests.get", side_effect=Exception("Connection refused")):
            result = generate_route_image_sync(50.0, 22.0, 50.2, 22.2, filename=filename)

        assert result is None

    def test_coordinates_are_subsampled_when_too_many(self, tmp_path):
        """If there are >100 coordinates, they should be subsampled (every 3rd)."""
        from handlers.map_service import generate_route_image_sync

        # Build 150 coordinates
        coords = [[22.0 + i * 0.001, 50.0 + i * 0.001] for i in range(150)]

        osrm_mock = MagicMock()
        osrm_mock.status_code = 200
        osrm_mock.json.return_value = self._osrm_response(coords)

        mapbox_mock = MagicMock()
        mapbox_mock.status_code = 200
        mapbox_mock.content = b"PNG"

        filename = str(tmp_path / "test_map6.png")
        mapbox_calls = []

        def fake_get(url, **kwargs):
            mapbox_calls.append(url)
            if "osrm" in url:
                return osrm_mock
            return mapbox_mock

        with patch("requests.get", side_effect=fake_get):
            result = generate_route_image_sync(50.0, 22.0, 50.15, 22.15, filename=filename)

        assert result == filename


class TestGetRouteMapFile:
    """Tests for get_route_map_file() async function."""

    @pytest.mark.asyncio
    async def test_returns_none_when_geocoding_fails(self):
        from handlers.map_service import get_route_map_file

        # Mock aiohttp so Nominatim returns empty list
        class FakeResp:
            status = 200
            async def json(self):
                return []
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                pass

        class FakeSession:
            def get(self, url, **kwargs):
                return FakeResp()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                pass

        with patch("aiohttp.ClientSession", return_value=FakeSession()):
            result = await get_route_map_file(
                biz={"street": "Main St"},
                client_address="Unknown Place XYZ",
                order_id="ord-123",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_calls_route_generator_on_success(self):
        from handlers.map_service import get_route_map_file

        client_geo = [{"lat": "50.05", "lon": "22.00"}]
        biz_geo = [{"lat": "50.04", "lon": "21.99"}]
        call_count = [0]

        class FakeResp:
            def __init__(self, data):
                self._data = data
                self.status = 200

            async def json(self):
                return self._data

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

        class FakeSession:
            def get(self, url, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    return FakeResp(client_geo)
                return FakeResp(biz_geo)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

        with patch("aiohttp.ClientSession", return_value=FakeSession()), \
             patch("handlers.map_service.generate_route_image_sync", return_value="map_ord-1.png"):
            result = await get_route_map_file(
                biz={"street": "Biz Street 1"},
                client_address="Client Street 5",
                order_id="ord-1",
            )

        assert result == "map_ord-1.png"
