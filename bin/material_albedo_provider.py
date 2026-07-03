#!/usr/bin/env python3
"""
Material Albedo Provider - G-buffer generator for Sora 2 / Cosmos training data.

Cluster C+: Provides per-pixel albedo, roughness, and metallic G-buffer data
for training data parity with Sora 2 / Cosmos pipelines.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Lazy imports
_np, _pil, _yaml = None, None, None

def _get_numpy() -> Any:
    global _np
    if _np is None:
        import numpy as np
        _np = np
    return _np

def _get_pil() -> Any:
    global _pil
    if _pil is None:
        from PIL import Image
        _pil = Image
    return _pil

def _get_yaml() -> Any:
    global _yaml
    if _yaml is None:
        import yaml
        _yaml = yaml
    return _yaml

@dataclass
class Material:
    name: str
    albedo: Optional[Any] = None
    roughness: Optional[Any] = None
    metallic: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

class MaterialAlbedoProvider:
    SUPPORTED_FORMATS = ('.png', '.jpg', '.jpeg', '.tiff', '.tif', '.exr', '.hdr')
    
    def __init__(self, output_dir: Optional[Path] = None):
        self.materials: Dict[str, Material] = {}
        self.output_dir = output_dir or Path(tempfile.mkdtemp(prefix='gbuffer_'))
    
    def load_from_directory(self, input_dir: Path) -> int:
        """Load materials from a directory containing material folders.

        Scans the input directory for subdirectories, each representing a material.
        Within each material folder, looks for texture files matching common naming
        patterns (e.g., albedo.*, roughness.*, metallic.*).

        Args:
            input_dir: Path to the directory containing material subdirectories.

        Returns:
            The number of materials successfully loaded.

        Raises:
            FileNotFoundError: If the input directory does not exist.
        """
        input_dir = Path(input_dir)
        if not input_dir.exists():
            raise FileNotFoundError(f"Input directory not found: {input_dir}")
        
        count = 0
        for item in input_dir.iterdir():
            if item.is_dir():
                material = Material(name=item.name)
                
                # Find texture files
                for pattern in ["albedo.*", "basecolor.*"]:
                    for f in item.glob(pattern):
                        if f.suffix.lower() in self.SUPPORTED_FORMATS:
                            material.albedo = self._load_image(f)
                            break
                
                for pattern in ["roughness.*", "rough.*"]:
                    for f in item.glob(pattern):
                        if f.suffix.lower() in self.SUPPORTED_FORMATS:
                            material.roughness = self._load_image(f, grayscale=True)
                            break
                
                for pattern in ["metallic.*", "metal.*"]:
                    for f in item.glob(pattern):
                        if f.suffix.lower() in self.SUPPORTED_FORMATS:
                            material.metallic = self._load_image(f, grayscale=True)
                            break
                
                if material.albedo is not None:
                    self.materials[item.name] = material
                    count += 1
                    logger.info(f"Loaded material: {item.name}")
        
        return count
    
    def load_from_yaml(self, yaml_path: Path) -> int:
        """Load materials from a YAML configuration file.

        Parses a YAML file containing material definitions with paths to texture
        images. Each material entry should have a 'name' and optional paths to
        albedo, roughness, and metallic texture files.

        Args:
            yaml_path: Path to the YAML configuration file.

        Returns:
            The number of materials successfully loaded.

        Raises:
            FileNotFoundError: If the YAML file does not exist.
        """
        yaml_path = Path(yaml_path)
        if not yaml_path.exists():
            raise FileNotFoundError(f"YAML file not found: {yaml_path}")
        
        yaml_module = _get_yaml()
        with open(yaml_path, 'r') as f:
            config = yaml_module.safe_load(f)
        
        if not config or 'materials' not in config:
            return 0
        
        count = 0
        for material_config in config['materials']:
            name = material_config.get('name')
            if not name:
                continue
            
            material = Material(name=name)
            
            for channel in ['albedo', 'roughness', 'metallic']:
                if channel in material_config:
                    path = Path(material_config[channel])
                    if path.exists():
                        setattr(material, channel, self._load_image(
                            path, grayscale=(channel != 'albedo')
                        ))
            
            if material.albedo is not None:
                self.materials[name] = material
                count += 1
        
        return count
    
    def _load_image(self, image_path: Path, grayscale: bool = False) -> Optional[Any]:
        try:
            pil_module = _get_pil()
            np_module = _get_numpy()
            
            img = pil_module.open(image_path)
            
            if grayscale:
                if img.mode not in ['L', '1', 'I', 'F']:
                    img = img.convert('L')
            else:
                if img.mode not in ['RGB', 'RGBA']:
                    img = img.convert('RGB')
            
            arr = np_module.array(img, dtype=np_module.float32)
            
            # Normalize to [0, 1]
            if arr.dtype == np_module.uint8:
                arr = arr / 255.0
            elif arr.dtype == np_module.uint16:
                arr = arr / 65535.0
            
            # Handle alpha channel
            if not grayscale and arr.shape[-1] == 4:
                arr = arr[..., :3]
            
            # Ensure correct shape
            if grayscale and len(arr.shape) == 3:
                arr = arr.mean(axis=-1, keepdims=True)
            
            return arr
            
        except Exception as e:
            logger.error(f"Failed to load image {image_path}: {e}")
            return None
    
    def generate_gbuffer(self, material_name: str) -> Optional[Path]:
        """Generate a G-buffer numpy array for a single material.

        Combines albedo, roughness, and metallic texture data into a single
        5-channel numpy array (RGB + roughness + metallic) suitable for
        Sora 2 / Cosmos training pipelines.

        Args:
            material_name: Name of the material to generate G-buffer for.

        Returns:
            Path to the saved .npy file, or None if generation failed.
        """
        if material_name not in self.materials:
            logger.error(f"Material not found: {material_name}")
            return None
        
        material = self.materials[material_name]
        
        if material.albedo is None:
            logger.error(f"Material {material_name} has no albedo data")
            return None
        
        np_module = _get_numpy()
        h, w = material.albedo.shape[:2]
        
        # Create G-buffer array (RGB + roughness + metallic)
        gbuffer = np_module.zeros((h, w, 5), dtype=np_module.float32)
        gbuffer[..., :3] = material.albedo[..., :3]
        
        if material.roughness is not None:
            roughness = material.roughness
            if len(roughness.shape) == 3:
                roughness = roughness.mean(axis=-1)
            gbuffer[..., 3] = roughness
        
        if material.metallic is not None:
            metallic = material.metallic
            if len(metallic.shape) == 3:
                metallic = metallic.mean(axis=-1)
            gbuffer[..., 4] = metallic
        
        output_path = self.output_dir / f"{material_name}_gbuffer.npy"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            np_module.save(output_path, gbuffer)
            logger.info(f"Saved G-buffer: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"Failed to save G-buffer: {e}")
            return None
    
    def generate_all_gbuffers(self) -> Dict[str, Path]:
        """Generate G-buffer outputs for all loaded materials.

        Iterates through all materials in the provider and generates
        per-pixel albedo, roughness, and metallic G-buffer data.

        Returns:
            Dict[str, Path]: Mapping of material names to their output
                G-buffer file paths.

        Example:
            >>> provider = MaterialAlbedoProvider()
            >>> provider.load_from_directory(Path("./materials"))
            >>> results = provider.generate_all_gbuffers()
            >>> for name, path in results.items():
            ...     print(f"Generated {name} -> {path}")
        """
        results = {}
        for material_name in self.materials:
            output_path = self.generate_gbuffer(material_name)
            if output_path:
                results[material_name] = output_path
        
        logger.info(f"Generated {len(results)} G-buffers")
        return results
    
    def validate_material(self, material_name: str) -> Tuple[bool, List[str]]:
        """Validate that a material has all required texture data.

        Checks that the material exists in the provider and that its albedo
        texture has a valid shape (3D with 3 or 4 channels for RGB/RGBA).

        Args:
            material_name: Name of the material to validate.

        Returns:
            A tuple of (is_valid, issues) where is_valid is True if the material
            passes all validation checks, and issues is a list of strings
            describing any validation failures.
        """
        if material_name not in self.materials:
            return False, [f"Material not found: {material_name}"]
        
        material = self.materials[material_name]
        issues = []
        
        if material.albedo is None:
            issues.append("Missing albedo data")
        elif len(material.albedo.shape) != 3 or material.albedo.shape[2] not in [3, 4]:
            issues.append(f"Invalid albedo shape: {material.albedo.shape}")
        
        return len(issues) == 0, issues
    
    def get_stats(self) -> Dict[str, Any]:
        stats = {
            'total_materials': len(self.materials),
            'materials_with_albedo': 0,
            'materials_with_roughness': 0,
            'materials_with_metallic': 0,
        }
        
        for material in self.materials.values():
            if material.albedo is not None:
                stats['materials_with_albedo'] += 1
            if material.roughness is not None:
                stats['materials_with_roughness'] += 1
            if material.metallic is not None:
                stats['materials_with_metallic'] += 1
        
        return stats

def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for the Material Albedo Provider CLI.

    Parses command-line arguments and orchestrates material loading and G-buffer
    generation. Supports loading materials from a directory structure or YAML
    configuration, with options to list, validate, or generate G-buffers.

    Args:
        argv: Command-line arguments. If None, uses sys.argv. Useful for testing.

    Returns:
        Exit code: 0 on success, 1 on failure.

    Examples:
        >>> main(['--input-dir', '/path/to/materials', '--list'])
        Loaded materials:
          - stone
          - wood
          0
    """
    parser = argparse.ArgumentParser(
        description='Material Albedo Provider - Generate G-buffers for Sora 2/Cosmos training data.'
    )
    parser.add_argument('--input-dir', type=Path, help='Directory containing material subdirectories.')
    parser.add_argument('--yaml-config', type=Path, help='YAML configuration file for materials.')
    parser.add_argument('--output-dir', type=Path, help='Output directory for G-buffers.')
    parser.add_argument('--material', type=str, help='Process specific material only.')
    parser.add_argument('--list', action='store_true', help='List loaded materials.')
    parser.add_argument('--validate', action='store_true', help='Validate all materials.')
    parser.add_argument('--stats', action='store_true', help='Show statistics.')
    
    args = parser.parse_args(argv)
    
    if not args.input_dir and not args.yaml_config:
        parser.error("Either --input-dir or --yaml-config must be specified")
    
    try:
        provider = MaterialAlbedoProvider(output_dir=args.output_dir)
        
        if args.input_dir:
            count = provider.load_from_directory(args.input_dir)
            logger.info(f"Loaded {count} materials from directory: {args.input_dir}")
        else:
            count = provider.load_from_yaml(args.yaml_config)
            logger.info(f"Loaded {count} materials from YAML: {args.yaml_config}")
        
        if count == 0:
            logger.error("No materials loaded")
            return 1
        
        if args.list:
            print("Loaded materials:")
            for name in provider.materials:
                print(f"  - {name}")
            return 0
        
        if args.validate:
            all_valid = True
            for name in provider.materials:
                is_valid, issues = provider.validate_material(name)
                if not is_valid:
                    all_valid = False
                    print(f"Material '{name}' has issues:")
                    for issue in issues:
                        print(f"  - {issue}")
            return 0 if all_valid else 1
        
        if args.stats:
            stats = provider.get_stats()
            print(f"Total materials: {stats['total_materials']}")
            print(f"With albedo: {stats['materials_with_albedo']}")
            print(f"With roughness: {stats['materials_with_roughness']}")
            print(f"With metallic: {stats['materials_with_metallic']}")
            return 0
        
        if args.material:
            output_path = provider.generate_gbuffer(args.material)
            if output_path:
                print(f"Generated G-buffer for '{args.material}': {output_path}")
                return 0
            else:
                return 1
        else:
            results = provider.generate_all_gbuffers()
            print(f"Generated {len(results)} G-buffers in: {provider.output_dir}")
            return 0
            
    except Exception as e:
        logger.error(f"Error: {e}")
        return 1

if __name__ == '__main__':
    sys.exit(main())