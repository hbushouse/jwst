""" Utilities for product manipulation."""

from collections import defaultdict, Counter
import logging
from pathlib import Path

from ...lib.suffix import remove_suffix
from .. import config
from . import diff

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# Duplicate association counter
# Used in function `prune_remove`
DupCount = 0


def sort_by_candidate(asns):
    """Sort associations by candidate

    Parameters
    ----------
    asns : [Association[,...]]
        List of associations

    Returns
    -------
    sorted_by_candidate : [Associations[,...]]
        New list of the associations sorted.

    Notes
    -----
    The current definition of candidates allows strictly lexigraphical
    sorting:
    aXXXX > cXXXX > oXXX

    If this changes, a comparison function will need be implemented
    """
    return sorted(asns, key=lambda asn: asn['asn_id'])


def get_product_names(asns):
    """Return product names from associations and flag duplicates

    Parameters
    ----------
    asns : [`Association`[, ...]]

    Returns
    -------
    product_names, duplicates : set(str[, ...]), [str[,...]]
        2-tuple consisting of the set of product names and the list of duplicates.
    """
    product_names = [
        asn['products'][0]['name']
        for asn in asns
    ]

    dups = [
        name
        for name, count in Counter(product_names).items()
        if count > 1
    ]
    if dups:
        logger.debug(
            'Duplicate product names: %s', dups
        )

    return set(product_names), dups


def prune(asns):
    """Remove duplicates and subset associations

    Situations where extraneous associations can occur are:

    - duplicate memberships
    - duplicate product names

    Associations with different product names but same memberships arise when
    different levels of candidates gather the same membership, such as
    OBSERVATION vs. GROUP. Associations of the lower level candidate are preferred.

    Associations with the same product name can occur in Level 2 when both an OBSERVATION
    candidate and a BACKGROUND candidate associations are created. The association that is
    a superset of members is the one chosen.

    Parameters
    ----------
    asns : [Association[,...]]
        Associations to prune

    Returns
    -------
    pruned : [Association[,...]]
        Pruned list of associations
    """
    pruned = prune_duplicate_associations(asns)
    pruned = prune_duplicate_products(pruned)
    return pruned

def prune_duplicate_associations(asns):
    """Remove duplicate associations in favor of lower level versions

    Main use case: For Level 3 associations, multiple associations with the
    same membership, but different levels, can be created. Remove duplicate
    associations of higher level.

    The assumption is that there is only one product per association, before
    merging.

    Parameters
    ----------
    asns : [Association[,...]]
        Associations to prune

    Returns
    -------
    pruned : [Association[,...]]
        Pruned list of associations

    """
    known_dups, valid_asns = identify_dups(asns)

    ordered_asns = sort_by_candidate(valid_asns)
    pruned = list()
    while True:
        try:
            original = ordered_asns.pop()
        except IndexError:
            break
        pruned.append(original)
        if original.asn_name.startswith('dup'):
            continue
        to_prune = list()
        for asn in ordered_asns:
            if asn.asn_name.startswith('dup'):
                continue
            try:
                diff.compare_product_membership(original['products'][0], asn['products'][0])
            except AssertionError:
                continue
            to_prune.append(asn)
        prune_remove(ordered_asns, to_prune, known_dups)

    return pruned + known_dups


def prune_duplicate_products(asns):
    """Remove duplicate products in favor of higher level versions

    The assumption is that there is only one product per association, before
    merging

    Parameters
    ----------
    asns: [Association[,...]]
        Associations to prune

    Returns
    pruned: [Association[,...]]
        Pruned list of associations

    """
    known_dups, valid_asns = identify_dups(asns)

    product_names, dups = get_product_names(valid_asns)
    if not dups:
        return asns

    ordered_asns = sort_by_candidate(asns)
    asn_by_product = defaultdict(list)
    for asn in ordered_asns:
        asn_by_product[asn['products'][0]['name']].append(asn)

    to_prune = list()
    for product in dups:
        dup_asns = asn_by_product[product]
        asn_keeper = dup_asns.pop()
        for asn in dup_asns:
            if asn.asn_name.startswith('dup'):
                continue
            try:
                diff.compare_product_membership(asn_keeper['products'][0], asn['products'][0])
            except diff.MultiDiffError as diffs:
                # If one is a pure subset, remove the smaller association.
                if len(diffs) == 1 and isinstance(diffs[0], diff.SubsetError):
                    if len(asn['products'][0]['members']) > len(asn_keeper['products'][0]['members']):
                        asn_keeper, asn = asn, asn_keeper
                    to_prune.append(asn)
                else:
                    # There are significant other differences.
                    # An acceptable case is "rate" vs. "rateints" as inputs.
                    if compare_nosuffix(asn_keeper, asn):
                        continue

                    # Something is different. Report but do not remove.
                    logger.warning('Following associations have the same product name but significant differences.')
                    logger.warning('Association 1: %s', asn_keeper)
                    logger.warning('Association 2: %s', asn)
                    logger.warning('Diffs: %s', diffs)
            else:
                # Associations are exactly the same. Discard the logically lesser one.
                # Due to the sorting, this should be the current `asn`
                to_prune.append(asn)

    prune_remove(ordered_asns, to_prune, known_dups)
    return ordered_asns + known_dups


def compare_nosuffix(left, right):
    """Check if the only difference is in rate vs rateints suffixes

    A valid situation is to have two associations be exactly the same except
    for the suffix used on all the science inputs. If one association uses
    "rate" and the other uses "rateints", this is OK.

    Parameters
    ----------
    left, right : Association, Association
        The associations to compare.

    Returns
    -------
    valid : bool
        True if the only difference is in the suffixes of the inputs.
    """
    if len(left['products']) != len(right['products']):
        return False

    for left_product in left['products']:
        left_sciences = set(exposure_name(member['expname'])[0]
                         for member in left_product['members']
                         if member['exptype'] == 'science')
        for right_product in right['products']:
            right_sciences = set(exposure_name(member['expname'])[0]
                              for member in right_product['members']
                              if member['exptype'] == 'science')
            if left_sciences == right_sciences:
                break
        else:
            # No right product matches the left product.
            # This is a fail.
            return False

    # Every left product has a matching right product.
    # Except for suffix, the associations are considered a match.
    return True


def exposure_name(path):
    """Extract the exposure name from a Stage 2 file name

    Parameters
    ----------
    path : Path or str
        The file name or path.

    Returns
    -------
    exposure : str
        The exposure name
    """
    path = Path(path)
    exposure = remove_suffix(path.stem)
    return exposure


def prune_remove(remove_from, to_remove, known_dups):
    """Remove or rename associations to be pruned

    Default behavior is to remove associations listed in the `to_remove`
    list from the `remove_from` list.

    However, if `config.DEBUG` is `True`, that association is simply
    renamed, adding the string "dupXXXXX" as a prefix to the association's
    name.

    Parameters
    ----------
    remove_from : [Association[,...]]
        The list of associations from which associations will be removed.
        List is modified in-place.

    to_remove : [Association[,...]]
        The list of associations to remove from the `remove_from` list.

    known_dups : [Association[,...]]
        Known duplicates. New ones are added by this function
        if debugging is in effect.
    """
    global DupCount

    if to_remove:
        logger.debug('Duplicate associations found: %s', to_remove)
    for asn in to_remove:
        remove_from.remove(asn)
        if config.DEBUG:
            DupCount += 1
            asn.asn_name = f'dup{DupCount:05d}_{asn.asn_name}'
            known_dups.append(asn)


def identify_dups(asns):
    """Separate associations based on whether they have already been identified as dups

    Parameters
    ----------
    asns: [Association[,...]]
        Associations to prune

    Returns
    identified, valid : [Association[,...]], [Association[,...]]
        Dup-identified and valid associations
    """
    identified = list()
    valid = list()
    for asn in asns:
        if asn.asn_name.startswith('dup'):
            identified.append(asn)
        else:
            valid.append(asn)
    return identified, valid
