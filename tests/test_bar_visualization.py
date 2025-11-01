"""
Unit tests for BAR content visualization functionality.
"""

import pytest
from src.device_clone.bar_content_generator import (
    BarContentGenerator,
    BarContentType,
)


class TestBarVisualization:
    """Test BAR visualization capabilities."""

    def test_entropy_calculation(self):
        """Test entropy calculation for various patterns."""
        gen = BarContentGenerator(device_signature="test-entropy-calc")
        
        # All zeros (minimum entropy)
        zeros = bytes(1024)
        entropy_zeros = gen._calculate_entropy(zeros)
        assert entropy_zeros == 0.0, "All zeros should have 0 entropy"
        
        # Uniform distribution (high entropy)
        uniform = bytes(range(256)) * 4
        entropy_uniform = gen._calculate_entropy(uniform)
        assert entropy_uniform == 8.0, "Uniform distribution should have max entropy"
        
        # Generated high-entropy content
        high_entropy = gen._get_seeded_bytes(4096, "test")
        entropy_high = gen._calculate_entropy(high_entropy)
        assert entropy_high > 7.5, f"Generated content should have high entropy (got {entropy_high})"

    def test_get_entropy_stats(self):
        """Test entropy statistics calculation."""
        gen = BarContentGenerator(device_signature="test-stats")
        
        # Empty data
        stats_empty = gen.get_entropy_stats(bytes())
        assert stats_empty["entropy"] == 0.0
        assert stats_empty["uniqueness"] == 0.0
        
        # Real content
        content = gen.generate_bar_content(4096, 0, BarContentType.MIXED)
        stats = gen.get_entropy_stats(content)
        
        assert "entropy" in stats
        assert "uniqueness" in stats
        assert "size" in stats
        assert "unique_bytes" in stats
        
        assert stats["entropy"] > 7.0, "Generated content should have high entropy"
        assert stats["size"] == 4096
        assert stats["unique_bytes"] > 240, "Should have most byte values represented"
        assert 0.0 <= stats["uniqueness"] <= 1.0

    def test_visualize_bar_content_small(self):
        """Test visualization skips small BARs."""
        gen = BarContentGenerator(device_signature="test-small-viz")
        small_bar = gen.generate_bar_content(512, 0, BarContentType.REGISTERS)
        
        # Should not raise, just skip visualization
        gen._visualize_bar_content(small_bar, 0)

    def test_visualize_bar_content_large(self):
        """Test visualization handles large BARs."""
        gen = BarContentGenerator(device_signature="test-large-viz")
        large_bar = gen.generate_bar_content(1024 * 1024, 0, BarContentType.MIXED)
        
        # Should complete without errors
        gen._visualize_bar_content(large_bar, 0)

    def test_visualize_empty_content(self):
        """Test visualization handles empty content gracefully."""
        gen = BarContentGenerator(device_signature="test-empty-viz")
        gen._visualize_bar_content(bytes(), 0)

    def test_ascii_entropy_rendering(self):
        """Test ASCII entropy rendering."""
        gen = BarContentGenerator(device_signature="test-ascii")
        
        samples = [
            (0x0000, 7.8),
            (0x1000, 6.5),
            (0x2000, 5.2),
            (0x3000, 7.9),
        ]
        
        # Should not raise
        gen._render_ascii_entropy(samples, 0)

    def test_generate_all_bars_with_visualization(self):
        """Test BAR generation with visualization enabled."""
        gen = BarContentGenerator(device_signature="test-gen-all-viz")
        
        bar_sizes = {
            0: 4096,      # Small register BAR
            1: 16384,     # Medium buffer BAR
            2: 1048576,   # Large mixed BAR
        }
        
        # Generate with visualization enabled
        bars = gen.generate_all_bars(bar_sizes, visualize=True)
        
        assert len(bars) == 3
        assert all(bar_idx in bars for bar_idx in [0, 1, 2])
        assert len(bars[0]) == 4096
        assert len(bars[1]) == 16384
        assert len(bars[2]) == 1048576

    def test_generate_all_bars_without_visualization(self):
        """Test BAR generation with visualization disabled."""
        gen = BarContentGenerator(device_signature="test-gen-all-no-viz")
        
        bar_sizes = {
            0: 2048,
            1: 8192,
        }
        
        # Generate with visualization disabled
        bars = gen.generate_all_bars(bar_sizes, visualize=False)
        
        assert len(bars) == 2
        assert len(bars[0]) == 2048
        assert len(bars[1]) == 8192

    def test_entropy_samples_coverage(self):
        """Test that entropy sampling covers the BAR appropriately."""
        gen = BarContentGenerator(device_signature="test-sample-coverage")
        
        # Create a large BAR with known patterns
        size = 64 * 1024  # 64KB
        content = gen.generate_bar_content(size, 0, BarContentType.MIXED)
        
        # Visualize and ensure it doesn't crash
        gen._visualize_bar_content(content, 0, max_samples=8)

    def test_visualization_determinism(self):
        """Test that visualization is deterministic for same signature."""
        sig = "deterministic-viz-test"
        
        gen1 = BarContentGenerator(device_signature=sig)
        gen2 = BarContentGenerator(device_signature=sig)
        
        bar1 = gen1.generate_bar_content(8192, 0, BarContentType.MIXED)
        bar2 = gen2.generate_bar_content(8192, 0, BarContentType.MIXED)
        
        # Content should be identical
        assert bar1 == bar2
        
        # Stats should be identical
        stats1 = gen1.get_entropy_stats(bar1)
        stats2 = gen2.get_entropy_stats(bar2)
        
        assert stats1["entropy"] == stats2["entropy"]
        assert stats1["uniqueness"] == stats2["uniqueness"]
        assert stats1["unique_bytes"] == stats2["unique_bytes"]

    def test_rich_import_fallback(self):
        """Test that visualization works even without Rich."""
        gen = BarContentGenerator(device_signature="test-fallback")
        
        # This should work regardless of Rich availability
        content = gen.generate_bar_content(4096, 0, BarContentType.BUFFER)
        gen._visualize_bar_content(content, 0)

    def test_entropy_color_ranges(self):
        """Test that entropy values map to correct visual ranges."""
        gen = BarContentGenerator(device_signature="test-color-ranges")
        
        # Test samples at different entropy levels
        samples_high = [(0, 7.8), (4096, 7.9)]  # Should be "green"
        samples_mid = [(0, 6.5), (4096, 6.8)]   # Should be "yellow"
        samples_low = [(0, 5.0), (4096, 5.5)]   # Should be "red"
        
        # Should not raise for any range
        gen._render_ascii_entropy(samples_high, 0)
        gen._render_ascii_entropy(samples_mid, 1)
        gen._render_ascii_entropy(samples_low, 2)

    def test_bar_content_types_all_visualize(self):
        """Test that all BAR content types can be visualized."""
        gen = BarContentGenerator(device_signature="test-all-types")
        
        types_and_sizes = [
            (BarContentType.REGISTERS, 4096),
            (BarContentType.BUFFER, 16384),
            (BarContentType.FIRMWARE, 8192),
            (BarContentType.MIXED, 32768),
        ]
        
        for content_type, size in types_and_sizes:
            content = gen.generate_bar_content(size, 0, content_type)
            # Should not raise
            gen._visualize_bar_content(content, 0)
            
            # Verify entropy is reasonable
            stats = gen.get_entropy_stats(content)
            assert stats["entropy"] > 5.0, f"{content_type} should have decent entropy"
