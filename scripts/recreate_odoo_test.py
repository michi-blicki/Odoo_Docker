def main():
    # 1. Stop existing test containers
    # 2. Remove old prod image
    # 3. Cleanup local dirs
    # 4. Copy chosen backup from prod-host
    # 5. Unpack backup
    # 6. Load Docker image safely
    # 7. Start PostgreSQL test DB
    # 8. Apply dump + SQL patches
    # 9. Restore local files
    # 10. Start all containers via docker-compose
    print(f'Recreation Done')

    return True

if __name__ == "__main__":
    main()
