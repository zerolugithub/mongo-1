/*-
 * See the file LICENSE for redistribution information.
 *
 * Copyright (c) 2008-2011 WiredTiger, Inc.
 *	All rights reserved.
 */

#include "wt_internal.h"

/*
 * __wt_col_search --
 *	Search a column-store tree for a specific record-based key.
 */
int
__wt_col_search(WT_SESSION_IMPL *session, WT_CURSOR_BTREE *cbt, int is_modify)
{
	WT_BTREE *btree;
	WT_COL *cip;
	WT_COL_REF *cref;
	WT_PAGE *page;
	uint64_t recno;
	uint32_t base, indx, limit;
	int ret;

	__cursor_search_clear(cbt);

	cbt->recno = recno = cbt->iface.recno;

	btree = session->btree;
	cref = NULL;

	/* Search the internal pages of the tree. */
	for (page = btree->root_page.page; page->type == WT_PAGE_COL_INT;) {
		WT_ASSERT(session, cref == NULL ||
		    cref->recno == page->u.col_int.recno);

		/* Binary search of internal pages. */
		for (base = 0,
		    limit = page->entries; limit != 0; limit >>= 1) {
			indx = base + (limit >> 1);
			cref = page->u.col_int.t + indx;

			if (recno == cref->recno)
				break;
			if (recno < cref->recno)
				continue;
			base = indx + 1;
			--limit;
		}
		WT_ASSERT(session, cref != NULL);

		/*
		 * Reference the slot used for next step down the tree.
		 *
		 * Base is the smallest index greater than recno and may be the
		 * (last + 1) index.  The slot for descent is the one before
		 * base.
		 */
		if (recno != cref->recno) {
			/*
			 * We don't have to correct for base == 0 because the
			 * only way for base to be 0 is if recno is the page's
			 * starting recno.
			 */
			WT_ASSERT(session, base > 0);
			cref = page->u.col_int.t + (base - 1);
		}

		/* Swap the parent page for the child page. */
		WT_ERR(__wt_page_in(session, page, &cref->ref, 0));
		__wt_page_release(session, page);
		page = WT_COL_REF_PAGE(cref);
	}

	WT_ASSERT(session, cref == NULL ||
	    cref->recno == page->u.col_leaf.recno);

	/*
	 * Copy the leaf page's write generation value before reading the page.
	 * Use a memory barrier to ensure we read the value before we read any
	 * of the page's contents.
	 */
	if (is_modify) {
		cbt->write_gen = page->write_gen;
		WT_MEMORY_FLUSH;
	}
	cbt->page = page;
	cbt->compare = 0;

	/*
	 * Search the leaf page.  We do not check in the search path for a
	 * record greater than the maximum record in the tree; in that case,
	 * we arrive here with a record that's impossibly large for the page.
	 */
	if (page->type == WT_PAGE_COL_FIX) {
		if (recno >= page->u.col_leaf.recno + page->entries) {
			cbt->recno = page->u.col_leaf.recno + page->entries;
			cbt->compare = -1;
			cbt->ins_head = WT_COL_APPEND(page);
		} else
			cbt->ins_head = WT_COL_UPDATE_SINGLE(page);
	} else {
		if ((cip = __col_var_search(page, recno)) == NULL) {
			cbt->recno = __col_last_recno(page);
			cbt->compare = -1;
			cbt->ins_head = WT_COL_APPEND(page);
		} else {
			cbt->slot = WT_COL_SLOT(page, cip);
			cbt->ins_head = WT_COL_UPDATE_SLOT(page, cbt->slot);
		}
	}

	/*
	 * Search the insert or append list for a match; __search_insert sets
	 * the return insert information appropriately.
	 */
	if (cbt->ins_head == NULL)
		cbt->ins = NULL;
	else
		if ((cbt->ins = __col_insert_search_stack(
		    cbt->ins_head, cbt->ins_stack, recno)) != NULL) {
			cbt->recno = WT_INSERT_RECNO(cbt->ins);
			if (recno == cbt->recno)
				cbt->compare = 0;
			else if (recno < cbt->recno)
				cbt->compare = 1;
			else
				cbt->compare = -1;
		}

	return (0);

err:	__wt_page_release(session, page);
	return (ret);
}
