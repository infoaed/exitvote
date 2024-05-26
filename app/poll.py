from dataclasses import dataclass

@dataclass
class poll_choices:
    """
    Convenience data structure to keep those crazy regexes beside the poll options data itself.
    """
    title: str = ""
    min: int = 0
    max: int = 1
    choices: list = None
    ordered: bool = False

    def regex(self):
        r = r"\s*\d*[.)]?\s*(("
        for i in range(len(self.choices)):
            r += r"\d*[.)]?\s*" + self.choices[i] + (r"|" if i < len(self.choices)-1 else r"")
        r += r")[ ]?[,]?[ ]?){%d,%d}\s*(;|$)" % (self.min, self.max)
        return r
            
    def fuzzy_regex(self):
        r = r"\s*\d*[.)]?\s*(("
        for i in range(len(self.choices)):
            r += r"\d*[.)]?\s*" + self.choices[i] + (r"|" if i < len(self.choices)-1 else r"")
        r += r"){e<=2}[ ]?[,]?[ ]?){%d,%d}\s*(;|$)" % (self.min, self.max)
        return r
