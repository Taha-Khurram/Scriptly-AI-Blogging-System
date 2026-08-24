#!/usr/bin/env python3
"""
Backfill Embeddings Script

Generates embeddings for all published blogs that don't have embeddings yet.
Run this script once to populate embeddings for existing content.

Usage:
    python scripts/backfill_embeddings.py [--user-id USER_ID] [--limit LIMIT]

Options:
    --user-id: Only process blogs for a specific user
    --limit: Maximum number of blogs to process (default: 100)
"""

import sys
import os
import argparse
import time

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app


def backfill_embeddings(user_id=None, limit=100):
    """Generate embeddings for blogs that don't have them."""
    app = create_app()

    with app.app_context():
        from app.firebase.firestore_service import FirestoreService
        from app.services.embedding_service import EmbeddingService

        db_service = FirestoreService()
        embedding_service = EmbeddingService()

        print("=" * 60)
        print("Embedding Backfill Script")
        print("=" * 60)

        # Get blogs without embeddings
        blogs = db_service.get_blogs_without_embeddings(user_id=user_id, limit=limit)

        if not blogs:
            print("\nNo blogs found without embeddings. All up to date!")
            return

        print(f"\nFound {len(blogs)} blog(s) without embeddings")
        print("-" * 60)

        success_count = 0
        error_count = 0

        for i, blog in enumerate(blogs, 1):
            blog_id = blog.get('id')
            title = blog.get('title', 'Untitled')[:50]

            print(f"[{i}/{len(blogs)}] Processing: {title}...")

            try:
                # Generate embedding
                embedding = embedding_service.generate_blog_embedding(blog)

                if embedding:
                    # Store embedding
                    result = db_service.update_blog_embedding(blog_id, embedding)

                    if result:
                        print(f"    [OK] Embedding generated ({len(embedding)} dimensions)")
                        success_count += 1
                    else:
                        print("    [FAIL] Failed to store embedding")
                        error_count += 1
                else:
                    print("    [FAIL] Failed to generate embedding")
                    error_count += 1

                # Rate limiting to avoid API quota issues
                time.sleep(0.5)

            except Exception as e:
                print(f"    [ERROR] {e}")
                error_count += 1

        print("\n" + "=" * 60)
        print("Backfill Complete!")
        print(f"  Success: {success_count}")
        print(f"  Errors: {error_count}")
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description='Backfill blog embeddings')
    parser.add_argument('--user-id', type=str, help='Process blogs for specific user only')
    parser.add_argument('--limit', type=int, default=100, help='Maximum blogs to process')

    args = parser.parse_args()

    backfill_embeddings(user_id=args.user_id, limit=args.limit)


if __name__ == '__main__':
    main()
