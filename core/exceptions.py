class ParsingError(Exception):
    pass


class UnsupportedMarketplaceError(ParsingError):
    pass


class CrawlerNotFoundError(ParsingError):
    pass


class ProductCardParsingException(Exception):
    pass
