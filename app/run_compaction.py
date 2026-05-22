from app.compaction import compact_memory


if __name__ == "__main__":
    result = compact_memory()
    print("Compactação concluída.")
    print(f"Tamanho da compact memory: {len(result)} caracteres.")